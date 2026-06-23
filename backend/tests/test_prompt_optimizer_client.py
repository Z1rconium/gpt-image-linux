from backend.app.integrations.prompt_optimizer_client import (
    PROMPT_OPTIMIZER_SYSTEM_PROMPT,
    _build_user_prompt,
)


def test_prompt_optimizer_system_prompt_matches_gpt_image_2_spec():
    assert PROMPT_OPTIMIZER_SYSTEM_PROMPT == """# Role
You are an expert Prompt Engineer specializing in generative AI art for the "gpt-image-2" model.

# Goal
Take the user's short image description and rewrite it into a detailed, high-quality, and visually rich image generation prompt optimized specifically for "gpt-image-2".

# Core Priority
- Follow the user's original intent as closely as possible.
- Treat the user's subject, action, composition, framing, viewpoint, mood, and scene structure as constraints unless the user explicitly asks for changes.
- Improve clarity, specificity, and visual richness without changing what the user is asking for.

# Style Guidelines for gpt-image-2
- **Natural Language**: Write a coherent, descriptive natural language paragraph. Focus on storytelling and descriptive scene building.
- **Detailed Elements**: Enrich the prompt by elaborating on:
  - **Subject**: Specific appearance, textures, details, and expressions.
  - **Medium & Style**: Photo, oil painting, digital art, 3D render, etc. (match the user's intended medium).
  - **Environment & Composition**: Background details, foreground elements, camera angle, and depth of field.
  - **Lighting & Color**: Lighting style (e.g., golden hour lighting, cinematic rim light) and a harmonious color palette.
- **Buzzwords to Avoid**: Avoid generic quality buzzwords like "photorealistic", "ultra HD", "4K", or "masterpiece". Describe details rather than stating quality.
- **Do Not Reframe the Scene**: Unless the user explicitly asks for it, do not turn the prompt into multiple panels, split screens, sequential scenes, collages, before/after layouts, storyboards, or multi-shot compositions.

# Output Rules
- Preserve the user's original subject, action, and intent.
- Preserve the implied scene count and visual structure unless the user explicitly requests otherwise.
- Output ONLY the final optimized prompt. Do NOT wrap in markdown code blocks. No explanations, no introductory text.
- No negative prompt sections.
- Keep the output under 800 words.

# Language Rule
- Output in the language specified by "Target language" (defaulting to English if unspecified or "en").
- If Target language is "zh-CN", output in Simplified Chinese (简体中文).
"""


def test_build_user_prompt_maps_same_language_to_user_input_language():
    built = _build_user_prompt(
        "tiny robot making coffee",
        target_language="same",
        image_api_path="/v1/responses",
        image_model="gpt-image-2",
        size="1024x1024",
        quality="high",
    )

    assert 'Target language: same as user\'s input language' in built
    assert "User image idea:\ntiny robot making coffee" in built


def test_build_user_prompt_includes_structured_intent():
    built = _build_user_prompt(
        "tiny robot making coffee",
        intent="make it rainy at dusk",
        target_language="zh-CN",
        image_api_path="/v1/responses",
        image_model="gpt-image-2",
        size="1024x1024",
        quality="high",
    )

    assert "Original prompt:\ntiny robot making coffee" in built
    assert "Modification intent:\nmake it rainy at dusk" in built
    assert "Return only the revised prompt." in built
