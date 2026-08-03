import sys, struct, shutil
sys.stdout.reconfigure(encoding='utf-8')

exe = r'C:\Users\azimd\OneDrive\Desktop\aziz\All\Vortex-Windows\Vortex\Vortex.exe'

with open(exe, 'rb') as f:
    data = f.read()

print(f"EXE boyutu: {len(data):,} bytes\n")

# TTF magic bytes: 00 01 00 00 veya OTTO (OTF)
TTF_MAGIC = b'\x00\x01\x00\x00'
OTF_MAGIC = b'OTTO'

# Inter string referanslarının yakınında TTF başlangıcı ara
ref_offsets = []
idx = 0
while True:
    i = data.find(b'Inter', idx)
    if i == -1: break
    ref_offsets.append(i)
    idx = i + 1

print(f"'Inter' toplam {len(ref_offsets)} yerde geçiyor")

# Her referansın çevresinde TTF magic ara
found_ttfs = []
for ref in ref_offsets[:50]:
    # 200KB geri git, TTF başlangıcı ara
    search_start = max(0, ref - 200000)
    search_end   = min(len(data), ref + 100)
    
    for magic in [TTF_MAGIC, OTF_MAGIC]:
        i = search_start
        while i < search_end:
            i = data.find(magic, i, search_end)
            if i == -1: break
            # TTF/OTF doğrulama: numTables field (offset 4, 2 bytes)
            num_tables = struct.unpack_from('>H', data, i+4)[0]
            if 3 <= num_tables <= 30:
                # Gerçek bir TTF olabilir
                if i not in [x[0] for x in found_ttfs]:
                    found_ttfs.append((i, magic, num_tables, abs(ref - i)))
            i += 1

# En yakın olanları göster
found_ttfs.sort(key=lambda x: x[3])
print("\nEXE içindeki gömülü TTF/OTF dosyaları (Inter referansına yakınlık sırasıyla):")
for offset, magic, tables, dist in found_ttfs[:10]:
    # Boyutu tahmin etmek için tabloya bak
    try:
        # numTables @ offset+4
        nt = struct.unpack_from('>H', data, offset+4)[0]
        # İlk tablo tag'ini oku
        first_tag = data[offset+12:offset+16].decode('ascii','replace')
        print(f"  offset={offset:,}  magic={magic}  tables={tables}  first_tag={first_tag}  dist_from_Inter={dist:,}")
    except: pass

# Ayrıca "Inter-Regular" ve "Inter-Bold" stringlerinin tam offsetlerini göster
print("\n--- 'Inter-Regular' ve 'Inter-Bold' string offsetleri ---")
for kw in [b'Inter-Regular', b'Inter-Bold', b'Inter Regular', b'Inter Bold']:
    i = 0
    while True:
        i = data.find(kw, i)
        if i == -1: break
        ctx = data[max(0,i-10):i+50].replace(b'\x00',b' ').decode('ascii','replace')
        print(f"  [{kw.decode()}] @{i:,}: {ctx}")
        i += len(kw)
