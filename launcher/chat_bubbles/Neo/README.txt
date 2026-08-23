# Custom Chat Bubble Skin Guide

Create your own custom Chat Bubble for Vortex!

## Files Required:
1. `bubble.png` -> The main speech bubble background (Recommended size: 512x512 or 512x256, transparent PNG).
2. `tail.png` -> The speech indicator triangle/tail pointing down to the character (Recommended size: 128x128 or 64x64, transparent PNG).
3. `config.json` -> Configuration file for text colors, username colors, and padding.

## Example config.json:
```json
{
  "name": "Your Theme Name",
  "author": "Your Name",
  "text_color": "#FFFFFF",
  "username_color": "#A78BFA",
  "background_color": "#000000",
  "border_radius": 12,
  "padding": 10
}
```

## How to add new themes:
1. Create a new folder inside `chat_bubbles/` (e.g. `chat_bubbles/MyCoolTheme/`).
2. Add your `bubble.png`, `tail.png`, and `config.json` inside it.
3. Open VortexStrap and select your theme from the Chat Bubble selector!
