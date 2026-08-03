import sys, struct
sys.stdout.reconfigure(encoding='utf-8')

exe = r'C:\Users\azimd\OneDrive\Desktop\aziz\All\Vortex-Windows\Vortex\Vortex.exe'

with open(exe, 'rb') as f:
    data = f.read()

print(f"EXE size: {len(data):,} bytes")

# Search for font names used in the app
keywords = [b'Inter', b'Roboto', b'font-face', b'Nunito', b'Poppins',
            b'Outfit', b'sans-serif', b'monospace', b'font-family',
            b'Inter,', b'Arial', b'Helvetica', b'ui-sans-serif']
for kw in keywords:
    i = data.find(kw)
    if i >= 0:
        ctx = data[max(0,i-30):i+100].replace(b'\x00',b'').decode('ascii','replace')
        print(f"[{kw.decode()}] offset={i}: ...{ctx}...")

print("\n--- Searching for ASAR header ---")
# Asar header: 4 bytes (4) + 4 bytes (header_size) + {files:{...}}
for i in range(0, min(len(data), 200_000_000), 4):
    if data[i:i+4] == b'\x04\x00\x00\x00':
        next4 = struct.unpack_from('<I', data, i+4)[0]
        if 10 < next4 < 500000:
            peek = data[i+8:i+18]
            if b'files' in peek or b'{' in peek:
                print(f"Possible ASAR at offset {i}, header_size={next4}")
                print(data[i+8:i+8+200].decode('ascii','replace'))
                break

print("\n--- CSS/JS references ---")
for kw in [b'.css', b'tailwind', b'index.js', b'main.js', b'renderer']:
    i = data.find(kw)
    if i >= 0:
        ctx = data[max(0,i-20):i+60].replace(b'\x00',b'').decode('ascii','replace')
        print(f"  [{kw.decode()}]: ...{ctx}...")
