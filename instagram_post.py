"""
instagram_post.py — Instagram image + caption generator for Alabama.

Pipeline:
  1. Load visual identity (context/image_style.md) + content-type logic
     (context/content_types.md)
  2. Ask GPT-4o to (a) pick the Instagram content type + artistic direction,
     then (b) write an image generation prompt applying both
  3. Call gpt-image-1 to generate the image (1024×1024, base64)
  4. Upload the image to Cloudinary (permanent public URL)
  5. Write an Instagram caption (Claude Sonnet or GPT-4o fallback)
  6. Push to Buffer (only if INSTAGRAM_ENABLED=true and INSTAGRAM_CHANNEL_ID is set)

ENV VARS required:
  OPENAI_API_KEY          — DALL-E 3 + GPT-4o
  CLOUDINARY_CLOUD_NAME   — Cloudinary upload target
  CLOUDINARY_API_KEY      — Cloudinary auth
  CLOUDINARY_API_SECRET   — Cloudinary auth

ENV VARS optional:
  ANTHROPIC_API_KEY       — Claude Sonnet for caption (falls back to GPT-4o)
  INSTAGRAM_CHANNEL_ID    — Buffer channel ID for Instagram
  INSTAGRAM_ENABLED       — set to "true" to push to Buffer (default: false)
  CONTEXT_DIR             — override for context/ directory
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import re
import time
from datetime import datetime, timezone

import requests

log = logging.getLogger(__name__)


# ─── Context loader ───────────────────────────────────────────────────────────

def _load_image_style() -> str:
    default_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "context")
    context_dir = os.environ.get("CONTEXT_DIR", default_dir)
    path = os.path.join(context_dir, "image_style.md")
    if not os.path.isfile(path):
        log.warning("context/image_style.md not found — using minimal style defaults.")
        return ""
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _load_content_types() -> str:
    default_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "context")
    context_dir = os.environ.get("CONTEXT_DIR", default_dir)
    path = os.path.join(context_dir, "content_types.md")
    if not os.path.isfile(path):
        log.warning("context/content_types.md not found — skipping content-type guidance.")
        return ""
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _load_author_context() -> str:
    default_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "context")
    context_dir = os.environ.get("CONTEXT_DIR", default_dir)
    chunks: list[str] = []
    for root, _, files in os.walk(context_dir):
        for fname in sorted(files):
            if fname.endswith(".md") and fname not in ("image_style.md", "content_types.md"):
                path = os.path.join(root, fname)
                with open(path, "r", encoding="utf-8") as f:
                    rel = os.path.relpath(path, context_dir)
                    chunks.append(f"## [{rel}]\n{f.read()}")
    return "\n\n---\n\n".join(chunks)


# ─── DALL-E prompt generator ──────────────────────────────────────────────────

def _select_content_brief(story: dict, content_types: str, oc) -> dict:
    """Step 1 — pick the Instagram content type, artistic direction and brief.

    Uses content_types.md to decide *what kind of image* to produce for this
    story on Instagram. Returns a dict with keys: content_type, direction,
    text_level, human_figures, visual_idea. Falls back to a sensible default
    if content_types.md is absent or the model returns malformed output.
    """
    if not content_types:
        return {
            "content_type": "Instagram short reflection post",
            "direction": "Direction 1 — Cabinet d'étude",
            "text_level": "none",
            "human_figures": "no",
            "visual_idea": "",
        }

    prompt = (
        f"CONTENT-TYPE DECISION GUIDE:\n{content_types}\n\n"
        f"You are choosing how to illustrate a daily NEWS story on INSTAGRAM only.\n"
        f"Using the guide above, decide the best approach for this story.\n\n"
        f"STORY:\n"
        f"Category : {story.get('category', '')}\n"
        f"Subject  : {story.get('subject', '')}\n"
        f"Summary  : {story.get('summary', '')}\n\n"
        f"Return ONLY a JSON object with these keys:\n"
        f'  "content_type"  : the chosen Instagram content type from the guide\n'
        f'  "direction"     : the chosen artistic direction (1, 2, 3 or 4) with its name\n'
        f'  "text_level"    : "none", "low", "medium" or "high"\n'
        f'  "human_figures" : "yes", "no" or "subtle"\n'
        f'  "visual_idea"   : one sentence describing the symbolic visual concept\n'
    )
    try:
        resp = oc.chat.completions.create(
            model="gpt-4o",
            max_tokens=300,
            response_format={"type": "json_object"},
            messages=[{"role": "user", "content": prompt}],
        )
        brief = json.loads(resp.choices[0].message.content)
        log.info(
            f"Instagram brief: {brief.get('content_type')} / {brief.get('direction')} / "
            f"text={brief.get('text_level')} / figures={brief.get('human_figures')}"
        )
        return brief
    except Exception as e:
        log.warning(f"Content-type selection failed ({e}) — using default brief.")
        return {
            "content_type": "Instagram short reflection post",
            "direction": "Direction 1 — Cabinet d'étude",
            "text_level": "none",
            "human_figures": "no",
            "visual_idea": "",
        }


def _generate_dalle_prompt(story: dict, image_style: str, brief: dict, oc) -> str:
    """Step 2 — write a DALL-E 3 prompt applying the visual identity + brief."""
    style_block = f"VISUAL IDENTITY GUIDELINES:\n{image_style}\n\n" if image_style else ""
    brief_block = (
        f"CONTENT BRIEF FOR THIS IMAGE (decided from the content-type guide):\n"
        f"- Content type   : {brief.get('content_type', '')}\n"
        f"- Artistic dir.  : {brief.get('direction', '')}\n"
        f"- Text in image  : {brief.get('text_level', 'none')}\n"
        f"- Human figures  : {brief.get('human_figures', 'no')}\n"
        f"- Visual idea    : {brief.get('visual_idea', '')}\n\n"
    )
    prompt = (
        f"{style_block}"
        f"{brief_block}"
        f"Write a DALL-E 3 image generation prompt for an Instagram post about the following story.\n\n"
        f"RULES:\n"
        f"- The image must be symbolic and evocative, NOT a literal illustration of the news.\n"
        f"- Apply the chosen artistic direction and visual identity above precisely.\n"
        f"- Honour the content brief: composition, emotional intensity and whether human figures appear.\n"
        f"- Write the prompt in English (DALL-E works best in English).\n"
        f"- The prompt must be a single paragraph of 40–80 words.\n"
        f"- Do NOT include any text or words inside the image.\n"
        f"- Do NOT mention brand names, logos, or real people by name.\n"
        f"- Return ONLY the prompt text, nothing else.\n\n"
        f"STORY:\n"
        f"Category : {story.get('category', '')}\n"
        f"Subject  : {story.get('subject', '')}\n"
        f"Summary  : {story.get('summary', '')}\n"
    )
    resp = oc.chat.completions.create(
        model="gpt-4o",
        max_tokens=200,
        messages=[{"role": "user", "content": prompt}],
    )
    dalle_prompt = resp.choices[0].message.content.strip()
    log.info(f"DALL-E prompt generated ({len(dalle_prompt)} chars): {dalle_prompt[:120]}…")
    return dalle_prompt


# ─── Image generation (gpt-image-1) ──────────────────────────────────────────

def _generate_image(dalle_prompt: str, oc) -> bytes:
    """Generate image with gpt-image-1 and return raw PNG bytes."""
    response = oc.images.generate(
        model="gpt-image-1",
        prompt=dalle_prompt,
        size="1024x1024",
        quality="standard",
        n=1,
    )
    image_bytes = base64.b64decode(response.data[0].b64_json)
    log.info(f"gpt-image-1 image generated ({len(image_bytes)} bytes)")
    return image_bytes


# ─── Cloudinary upload ────────────────────────────────────────────────────────

def _upload_to_cloudinary(image_bytes: bytes) -> str:
    """Upload raw image bytes to Cloudinary and return the permanent URL."""
    cloud_name  = os.environ["CLOUDINARY_CLOUD_NAME"]
    api_key     = os.environ["CLOUDINARY_API_KEY"]
    api_secret  = os.environ["CLOUDINARY_API_SECRET"]

    timestamp = str(int(time.time()))
    folder = "alabama"
    params_to_sign = f"folder={folder}&timestamp={timestamp}"
    signature = hashlib.sha1(f"{params_to_sign}{api_secret}".encode()).hexdigest()

    resp = requests.post(
        f"https://api.cloudinary.com/v1_1/{cloud_name}/image/upload",
        data={
            "timestamp": timestamp,
            "api_key": api_key,
            "signature": signature,
            "folder": folder,
        },
        files={"file": ("image.png", image_bytes, "image/png")},
        timeout=60,
    )
    resp.raise_for_status()
    permanent_url = resp.json()["secure_url"]
    log.info(f"Image uploaded to Cloudinary: {permanent_url}")
    return permanent_url


# ─── Caption writer ───────────────────────────────────────────────────────────

def _write_caption(
    story: dict,
    author_context: str,
    is_owned: bool,
    oc,
) -> str:
    """Write an Instagram caption for the story."""
    context_block = (
        f"ABOUT THE AUTHOR:\n{author_context}\n\n---\n\n" if author_context else ""
    )
    if is_owned:
        voice_note = (
            "This is the author's OWN content. Write in first person ('I', 'my'). "
            "Warm, personal, and inviting — promoting the author's work without aggressive marketing.\n"
        )
    else:
        voice_note = (
            "Write as an observer sharing a thoughtful reflection on this story. "
            "Third person, contemplative, no corporate language.\n"
        )

    prompt = (
        f"{context_block}"
        f"Write an Instagram caption for the following story.\n\n"
        f"RULES:\n"
        f"- {voice_note}"
        f"- Maximum 150 words.\n"
        f"- Short paragraphs, breathing rhythm.\n"
        f"- 3 to 5 relevant hashtags at the very end.\n"
        f"- No engagement-bait questions.\n"
        f"- No 'game-changer', 'revolutionary', 'disruptive', 'excited to share'.\n"
        f"- Match the language of the story (French or English).\n"
        f"- Return ONLY the caption text, nothing else.\n\n"
        f"STORY:\n"
        f"Subject : {story.get('subject', '')}\n"
        f"Summary : {story.get('summary', '')}\n"
        f"Source  : {story.get('source', '')}\n"
    )

    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if anthropic_key:
        from anthropic import Anthropic
        client = Anthropic(api_key=anthropic_key)
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=400,
            timeout=40.0,
            messages=[{"role": "user", "content": prompt}],
        )
        caption = response.content[0].text.strip()
        log.info("Instagram caption written via Claude Sonnet")
    else:
        resp = oc.chat.completions.create(
            model="gpt-4o",
            max_tokens=400,
            messages=[{"role": "user", "content": prompt}],
        )
        caption = resp.choices[0].message.content.strip()
        log.info("Instagram caption written via GPT-4o")

    return caption


# ─── Buffer push ──────────────────────────────────────────────────────────────

def _push_to_buffer(caption: str, image_url: str, channel_id: str) -> bool:
    token = os.environ.get("BUFFER_ACCESS_TOKEN", "")
    if not token or not channel_id:
        log.error("Cannot push to Instagram: BUFFER_ACCESS_TOKEN or INSTAGRAM_CHANNEL_ID missing.")
        return False

    text_json  = json.dumps(caption)
    image_json = json.dumps(image_url)
    mutation = f"""
    mutation CreatePost {{
      createPost(input: {{
        text: {text_json},
        channelId: "{channel_id}",
        media: {{ url: {image_json} }},
        schedulingType: automatic,
        mode: addToQueue
      }}) {{
        ... on PostActionSuccess {{
          post {{ id }}
        }}
        ... on MutationError {{
          message
        }}
      }}
    }}
    """
    try:
        resp = requests.post(
            "https://api.buffer.com",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}",
            },
            json={"query": mutation},
            timeout=15,
        )
        data = resp.json()
        if data.get("errors"):
            raise Exception(json.dumps(data["errors"]))
        post_result = data.get("data", {}).get("createPost", {})
        if post_result.get("post", {}).get("id"):
            log.info(f"Instagram post queued in Buffer — ID: {post_result['post']['id']}")
            return True
        raise Exception(post_result.get("message", json.dumps(data)))
    except Exception as e:
        log.error(f"Buffer Instagram push failed: {e}")
        draft_path = os.path.join(os.path.dirname(__file__), "instagram_drafts.txt")
        with open(draft_path, "a") as f:
            f.write(f"\n{'='*60}\n")
            f.write(f"{datetime.now(timezone.utc).isoformat()}\n")
            f.write(f"IMAGE: {image_url}\n")
            f.write(f"CAPTION:\n{caption}\n")
        log.info("Instagram draft saved to instagram_drafts.txt")
        return False


# ─── Main entry point ─────────────────────────────────────────────────────────

def generate_and_push_instagram(
    stories: list[dict],
    owned_source_labels: set | None = None,
) -> dict | None:
    """
    Full pipeline: select story → generate image → upload → write caption → push.

    Uses the same top story as the LinkedIn post (highest grade).
    Returns a result dict or None if skipped.

    Result dict keys:
      story, content_type, direction, dalle_prompt, image_url, caption,
      instagram_pushed, instagram_enabled
    """
    if not stories:
        log.info("Instagram: no stories — skipping.")
        return None

    required = ["OPENAI_API_KEY", "CLOUDINARY_CLOUD_NAME", "CLOUDINARY_API_KEY", "CLOUDINARY_API_SECRET"]
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        log.warning(f"Instagram: missing env vars {missing} — skipping.")
        return None

    import openai
    oc = openai.OpenAI(api_key=os.environ["OPENAI_API_KEY"])

    # Pick the top story (same one LinkedIn would pick if it had to pick one)
    story = max(stories, key=lambda s: int(s.get("grade", 0)))

    is_owned = (
        story.get("is_owned_source")
        or (owned_source_labels and story.get("from_newsletter", "") in owned_source_labels)
    )

    image_style    = _load_image_style()
    content_types  = _load_content_types()
    author_context = _load_author_context()

    try:
        # 1. Decide content type + artistic direction (content_types.md)
        brief = _select_content_brief(story, content_types, oc)

        # 2. Generate DALL-E prompt (image_style.md + brief)
        dalle_prompt = _generate_dalle_prompt(story, image_style, brief, oc)

        # 3. Generate image
        temp_url = _generate_image(dalle_prompt, oc)

        # 4. Upload to Cloudinary
        permanent_url = _upload_to_cloudinary(temp_url)

        # 5. Write caption
        caption = _write_caption(story, author_context, is_owned, oc)
        log.info(f"Instagram caption ({len(caption)} chars):\n{caption[:200]}…")

    except Exception as e:
        log.error(f"Instagram generation failed: {e}", exc_info=True)
        return None

    # 6. Push to Buffer (only if enabled)
    instagram_enabled = os.environ.get("INSTAGRAM_ENABLED", "false").lower() == "true"
    channel_id = os.environ.get("INSTAGRAM_CHANNEL_ID", "")
    instagram_pushed = False

    if instagram_enabled and channel_id:
        instagram_pushed = _push_to_buffer(caption, permanent_url, channel_id)
    elif instagram_enabled and not channel_id:
        log.warning("INSTAGRAM_ENABLED=true but INSTAGRAM_CHANNEL_ID not set — skipping push.")
    else:
        log.info("Instagram content generated but INSTAGRAM_ENABLED is not true — skipping push.")

    return {
        "story":               story,
        "content_type":        brief.get("content_type", ""),
        "direction":           brief.get("direction", ""),
        "dalle_prompt":        dalle_prompt,
        "image_url":           permanent_url,
        "caption":             caption,
        "instagram_pushed":    instagram_pushed,
        "instagram_enabled":   instagram_enabled,
    }
