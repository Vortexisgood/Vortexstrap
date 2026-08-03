import sys, struct
sys.stdout.reconfigure(encoding='utf-8')

exe = r'C:\Users\azimd\OneDrive\Desktop\aziz\All\Vortex-Windows\Vortex\Vortex.exe'

with open(exe, 'rb') as f:
    data = f.read()

# "Inter" kelimesinin TÜM geçtiği yerleri bul
print("=== 'Inter' font referansları ===")
idx = 0
count = 0
while count < 30:
    idx = data.find(b'Inter', idx)
    if idx == -1:
        break
    # Sadece font ismi gibi görünen yerleri göster
    # (null byte'lardan öncesi/sonrası)
    before = data[max(0,idx-40):idx].replace(b'\x00', b' ')
    after  = data[idx:idx+50].replace(b'\x00', b' ')
    line   = (before + after).decode('ascii', 'replace')
    # Sadece font gibi gözükenler
    if any(x in line for x in ['font', 'Font', 'family', 'face', 'Regular', 'Bold', 'Medium', 'Light']):
        print(f"  [{idx}]: {line[:120].strip()}")
    idx += 5
    count += 1

# Bevy/egui font kayıtları
print("\n=== Egui / font loader referansları ===")
for kw in [b'egui', b'FontData', b'font_data', b'default_fonts',
           b'proportional', b'monospace', b'\"Inter\"', b"'Inter'",
           b'NotoSans', b'Ubuntu', b'Hack', b'JetBrains']:
    i = data.find(kw)
    if i >= 0:
        ctx = data[max(0,i-30):i+80].replace(b'\x00', b'').decode('ascii','replace')
        print(f"  [{kw.decode()}] @{i}: {ctx}")

# Büyük harflerle yazılmış font isimleri
print("\n=== Olası ana font yeri ===")
for kw in [b'Inter\x00', b'Inter\x20', b'\"Inter', b'Inter,']:
    i = 0
    while True:
        i = data.find(kw, i)
        if i == -1: break
        ctx = data[max(0,i-60):i+80].replace(b'\x00',b' ').decode('ascii','replace')
        print(f"  [{kw}] @{i}: ...{ctx}...")
        i += len(kw)
