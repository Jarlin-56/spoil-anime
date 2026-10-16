import json
import math
import sys
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

if sys.stdout:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if sys.stderr:
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

from config import PROJECTS_DIR

def create_gradient_scene(width: int, height: int, top_color, bottom_color, title: str, subtitle: str, out_path: str):
    base = Image.new('RGB', (width, height), top_color)
    draw = ImageDraw.Draw(base)
    
    # Smooth vertical gradient
    for y in range(height):
        ratio = y / height
        r = int(top_color[0] * (1 - ratio) + bottom_color[0] * ratio)
        g = int(top_color[1] * (1 - ratio) + bottom_color[1] * ratio)
        b = int(top_color[2] * (1 - ratio) + bottom_color[2] * ratio)
        draw.line([(0, y), (width, y)], fill=(r, g, b))
        
    # Draw cinematic manga speed lines / anime aesthetic overlay
    cx, cy = width // 2, height // 2
    for angle_deg in range(0, 360, 4):
        rad = math.radians(angle_deg)
        x2 = cx + math.cos(rad) * width
        y2 = cy + math.sin(rad) * height
        alpha_val = int(25 + (angle_deg % 12) * 8)
        draw.line([(cx, cy), (x2, y2)], fill=(255, 255, 255, alpha_val), width=2)
        
    # Center cinematic banner
    draw.rectangle([100, height - 260, width - 100, height - 100], fill=(10, 15, 28))
    draw.rectangle([100, height - 260, width - 100, height - 100], outline=(0, 240, 255), width=3)
    
    # Add text
    draw.text((140, height - 230), title, fill=(255, 255, 255))
    draw.text((140, height - 170), subtitle, fill=(0, 240, 255))
    
    # Top badge
    draw.rectangle([80, 60, 360, 120], fill=(255, 0, 85))
    draw.text((100, 75), "ANIME RECAP STUDIO", fill=(255, 255, 255))
    
    base.save(out_path, quality=95)

def setup_demo_project():
    demo_dir = PROJECTS_DIR / "demo_project"
    frames_dir = demo_dir / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    
    scenes = [
        ("frame_0001.jpg", (40, 10, 20), (140, 20, 40), "SCENE 01: THE AWAKENING", "ท้องฟ้าสีเลือดกับสัญญาณเตือนภัยระดับหายนะ"),
        ("frame_0002.jpg", (10, 20, 50), (30, 80, 160), "SCENE 02: THE MONSTER HORDE", "เหล่าสัตว์อสูรโบราณทะลักออกมาจากรอยแยกมิติ"),
        ("frame_0003.jpg", (20, 10, 40), (80, 20, 100), "SCENE 03: THE LONE WARRIOR", "ชายหนุ่มชุดดำผู้ก้าวออกมารับมือเพียงลำพัง"),
        ("frame_0004.jpg", (0, 40, 60), (0, 180, 220), "SCENE 04: AZURE THUNDER BLADE", "การฟาดฟันดาบสายฟ้าที่สะกดทุกสายตา"),
        ("frame_0005.jpg", (60, 30, 0), (220, 140, 10), "SCENE 05: THE LEGEND RESTORED", "การทวงคืนเกียรติยศของผู้กล้าที่แท้จริง")
    ]
    
    manifest = []
    for idx, (fname, top_c, bot_c, t, subt) in enumerate(scenes, 1):
        fpath = frames_dir / fname
        create_gradient_scene(1920, 1080, top_c, bot_c, t, subt, str(fpath))
        manifest.append({
            "id": f"frame_{idx:04d}",
            "filename": fname,
            "path": f"projects/demo_project/frames/{fname}",
            "index": idx
        })
        
    with open(demo_dir / "frames_manifest.json", "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
        
    # Create sample script
    demo_script = {
        "title": "สปอยล์ผู้กล้าสายฟ้าฟาด ตอนที่ 1",
        "voice": "th-TH-NiwatNeural",
        "items": [
            {
                "id": 1,
                "image_id": "frame_0001",
                "text": "เรื่องราวเริ่มต้นขึ้นในวันที่ท้องฟ้าเปลี่ยนเป็นสีเลือด สัญญาณเตือนภัยดังระงมไปทั่วทั้งอาณาจักร",
                "action_type": "shock"
            },
            {
                "id": 2,
                "image_id": "frame_0002",
                "text": "เหล่าสัตว์อสูรโบราณนับหมื่นตัว ทะลักออกมาจากรอยแยกมิติอย่างไม่ทันตั้งตัว",
                "action_type": "battle"
            },
            {
                "id": 3,
                "image_id": "frame_0003",
                "text": "ชาวเมืองต่างพากันวิ่งหนีเอาชีวิตรอด แต่ในวินาทีนั้นเอง ชายหนุ่มชุดดำก็ได้ก้าวออกมา",
                "action_type": "normal"
            },
            {
                "id": 4,
                "image_id": "frame_0004",
                "text": "เพียงแค่เขาปลดปล่อยดาบสายฟ้าออกมา อสูรทั้งฝูงก็แหลกสลายกลายเป็นผุยผงในพริบตาเดียว!",
                "action_type": "battle"
            },
            {
                "id": 5,
                "image_id": "frame_0005",
                "text": "และนี่คือจุดเริ่มต้นของการทวงคืนบัลลังก์ กดติดตามช่องไว้ แล้วรอพบกับความมันส์ตอนต่อไปได้เลยครับ",
                "action_type": "drama"
            }
        ]
    }
    
    with open(demo_dir / "script.json", "w", encoding="utf-8") as f:
        json.dump(demo_script, f, ensure_ascii=False, indent=2)
        
    print(f"[+] Demo project initialized at: {demo_dir}")

if __name__ == "__main__":
    setup_demo_project()
