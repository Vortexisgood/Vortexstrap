from PIL import Image
import os

src = r"C:\Users\azimd\OneDrive\Desktop\aziz\All\Vortex-Windows\Vortex\launcher\Mouseİmleci\IMG_1330.gif"
dst = r"C:\Users\azimd\OneDrive\Desktop\aziz\All\Vortex-Windows\Vortex\launcher\Mouseİmleci\cursor.gif"

SIZE = 48  # cursor size in pixels

gif = Image.open(src)
frames = []
durations = []

try:
    while True:
        frame = gif.convert("RGBA")
        
        # Sample background color from corner pixels
        w, h = frame.size
        corners = [
            frame.getpixel((0, 0)),
            frame.getpixel((w - 1, 0)),
            frame.getpixel((0, h - 1)),
            frame.getpixel((w - 1, h - 1)),
        ]
        # Pick most common corner color as background
        bg = max(set(corners), key=corners.count)
        bg_r, bg_g, bg_b = bg[0], bg[1], bg[2]

        data = frame.getdata()
        new_data = []
        for pixel in data:
            r, g, b, a = pixel
            # If pixel is close to background color, make transparent
            if abs(r - bg_r) < 30 and abs(g - bg_g) < 30 and abs(b - bg_b) < 30:
                new_data.append((r, g, b, 0))
            else:
                new_data.append((r, g, b, a))

        frame.putdata(new_data)
        frame = frame.resize((SIZE, SIZE), Image.LANCZOS)
        frames.append(frame)

        try:
            durations.append(gif.info.get("duration", 80))
        except:
            durations.append(80)

        gif.seek(gif.tell() + 1)
except EOFError:
    pass

if frames:
    frames[0].save(
        dst,
        save_all=True,
        append_images=frames[1:],
        loop=0,
        duration=durations,
        disposal=2,
    )
    print(f"Done! {len(frames)} frames → {dst}")
else:
    print("No frames found!")
