# 🎬 Anime Recap Pipeline (Motion Comic & Asset Provenance Engine)

ระบบอัตโนมัติสำหรับการผลิตวิดีโอสรุป/สปอยล์อนิเมะด้วย AI สไตล์ **Motion Comic / Dynamic Keyframe Recap** พร้อมระบบตรวจสอบสิทธิ์และติดตามที่มาของสินทรัพย์สื่อ (Asset Provenance & Publication Readiness) เพื่อการผลิตสื่อเชิงวิจารณ์และสรุปเนื้อหาอย่างโปร่งใสและตรวจสอบได้

> ⚠️ **ข้อจำกัดความรับผิดชอบทางกฎหมาย (Legal Disclaimer):**  
> การดัดแปลงภาพ (Ken Burns, Film Grain, Color Grading) หรือการตัดเฉพาะภาพนิ่ง **ไม่ถือเป็นการอนุญาตทางลิขสิทธิ์โดยอัตโนมัติ** และระบบนี้ **ไม่สามารถรับประกันความปลอดภัยทางกฎหมาย การคุ้มครอง Fair Use หรือการปลอดจากการตรวจจับ Content ID / Rights Manager ได้** ระบบนี้ทำหน้าที่เป็นเครื่องมือช่วยตัดสินใจ (Decision-Support Tool) และผู้ใช้งานต้องตรวจสอบสิทธิ์ในสื่อต้นฉบับและเสียงดนตรีก่อนการเผยแพร่สู่สาธารณะเสมอ

---

## 🌟 จุดเด่นของระบบ (Key Highlights)
1. **📋 Asset Provenance & Publication-Readiness Review:**
   - ติดตามที่มาและสายวิวัฒนาการของสินทรัพย์สื่อทุกชิ้นตั้งแต่ วิดีโอต้นฉบับ → ภาพสกัด → คลิปเคลื่อนไหว → วิดีโอส่งออกฉบับสมบูรณ์
   - จัดการและบันทึกหลักฐานสัญญาอนุญาต (License, Attribution, Commercial Use) และสถานะสิทธิ์อย่างโปร่งใส (`needs_review`, `authorized`, `restricted`, `expired`)
   - ระบบประเมินความพร้อมก่อนเผยแพร่ (Publication-Readiness Gate) ที่แจ้งเตือนสินทรัพย์ที่ยังไม่ได้รับการอนุมัติอย่างชัดเจน
2. **🎞️ Motion Comic Keyframe Engine:**
   - สกัดเฉพาะภาพนิ่งสำคัญ (Keyframes) ตามการเปลี่ยนฉากและตรวจจับจังหวะ OP/ED
   - สร้างภาพเคลื่อนไหวแบบ **Dynamic Ken Burns (Slow Zoom-In, Zoom-Out, Pan, Tilt)** พร้อมการควบคุมไทม์ไลน์ระดับตัวอย่างคลื่นเสียง (Sample-Accurate A/V Sync)
   - ปรับแต่ง Color Grading และ Grain ให้วิดีโอน่าสนใจตามสไตล์ Cinematic Motion Comic
3. **🎙️ AI Thai Voiceover & Styled Subtitles:**
   - สังเคราะห์เสียงพากย์ภาษาไทยผ่าน Edge-TTS พร้อมบันทึก SentenceBoundary ระดับมิลลิวินาที
   - ผสมเสียงดนตรีประกอบ (BGM) พร้อม **Auto Audio Ducking** อัตโนมัติ
   - ฝังซับไตเติลภาษาไทยด้วยฟอนต์ Kanit ภายใต้สัญญาอนุญาตเปิด SIL Open Font License 1.1
4. **📱 Dual Format Export:**
   - **16:9 แนวนอน (1080p):** สำหรับ YouTube คลิปยาว 10–25 นาที
   - **9:16 แนวตั้ง (1080x1920):** สำหรับ Facebook Reels, YouTube Shorts, และ TikTok
5. **🖥️ Human QC Web Studio:**
   - หน้าแดชบอร์ดสไตล์ Cyber-Anime Dark Theme
   - ตรวจสอบ Storyboard, แก้ไขบทบรรยาย, พรีวิวเสียง และตรวจสอบสิทธิ์/ความพร้อมของสื่อก่อนเผยแพร่
   - เรนเดอร์วิดีโอแบบ Transaction-Safe พร้อมระบบกู้คืนอัตโนมัติ

---

## 🚀 วิธีเปิดใช้งานระบบ (Quick Start)

### 1. เปิด Web QC Studio
เปิด Terminal ในโฟลเดอร์นี้ แล้วรันคำสั่ง:
```bash
npm start
```
จากนั้นเปิดบราวเซอร์ไปที่: **`http://localhost:3000`**

---

## 📂 โครงสร้างโฟลเดอร์ในระบบ
```
สปอย/
├── core/                       # แกนประมวลผลหลัก (Python + FFmpeg)
│   ├── config.py               # การตั้งค่า Path และ Dependencies
│   ├── extractor.py            # ตัวสกัดภาพนิ่งจากวิดีโออนิเมะ (Scene Detection)
│   ├── tts_engine.py           # ตัวสร้างเสียงพากย์ Edge-TTS และไฟล์ซับไตเติล
│   ├── motion_compositor.py    # ตัวรวมภาพเป็นวิดีโอเคลื่อนไหว (Ken Burns + BGM + SFX)
│   ├── script_generator.py     # ตัวเขียนบท AI (Gemini) หรือ Template
│   └── audio_assets.py         # ตัวสร้างเพลง BGM และ SFX ลิขสิทธิ์ฟรี
├── server/                     # Backend API Server (Node.js Express)
│   └── server.js
├── web/                        # Frontend UI (HTML5, CSS3, JS)
│   ├── index.html
│   ├── style.css
│   └── app.js
├── assets/                     # แหล่งเก็บ BGM, SFX, และฟอนต์
├── projects/                   # โฟลเดอร์เก็บโปรเจกต์ของแต่ละเรื่อง
│   └── demo_project/           # โปรเจกต์ตัวอย่างที่เรนเดอร์พร้อมดูได้ทันที
└── package.json
```

---

## 💡 ขั้นตอนการทำคลิปใหม่ใน 3 ขั้นตอน
1. **สกัดภาพนิ่ง:** คลิกปุ่ม `+ สร้างโปรเจกต์ใหม่` จากนั้นคลิก `สกัดภาพจากวิดีโออนิเมะ` ระบุที่อยู่ไฟล์วิดีโอตอนนั้นๆ
2. **เขียนบท/พากย์เสียง:** พิมพ์เนื้อเรื่องภาษาไทย หรือกด `ให้ AI (Gemini) ช่วยเขียนบท` จากนั้นคลิก `สร้างเสียงพากย์ & ซับไตเติล`
3. **เรนเดอร์วิดีโอ:** เลือกสัดส่วนที่ต้องการ (16:9 หรือ 9:16) แล้วกด `เริ่มเรนเดอร์วิดีโอ` เมื่อเสร็จสามารถดูวิดีโอและกดดาวน์โหลดได้ทันที!
