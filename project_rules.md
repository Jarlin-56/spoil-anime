# Project Rules & Strict Constraints
*Note to AI: You must STRICTLY adhere to these rules before modifying any code. Never overwrite or ignore these constraints.*

## 1. Core Principles
- Always maintain existing working logic. Do not rewrite or refactor entire functions unless explicitly requested.
- Before applying a fix, check if it conflicts with any rules below.

## 2. Bugfix & Logic Constraints
(New rules extracted from bug fixes will be appended here)
- การตรวจจับและตัด OP/ED ต้องใช้ Multi-Stage Hybrid (Scene Cut + Audio + Credit) พร้อมล็อกปกป้องฉาก Cold Open และ Post-Credit เสมอ ห้ามตัดแบบ fixed-duration หรือตัดถึงท้ายคลิปโดยไม่ตรวจสอบ
- การซิงก์ภาพและเสียง (A/V Sync) ต้องใช้ Audio-Driven Master Timeline โดยคำนวณจากพิกัดเวลาสัมบูรณ์ (Global Timeline) รอยต่อภาพ Snap เข้าหาเฟรมที่ใกล้ที่สุด (Zero Cumulative Drift) และส่งออกเสียงเป็น Continuous Stems (PCM WAV 48kHz) เพื่อเสียงพูดธรรมชาติ ห้ามต่อเสียงแบบ Bitstream Copy หรือยัด Silence Pad บังคับตัดทอนจังหวะเสียงพากย์
- การจำกัดจำนวนภาพสกัด (Max Frames) ต้องรวบรวมและกรอง Candidate ตลอดทั้งคลิปก่อน แล้วใช้ Uniform Timeline Distribution กระจายตัวสม่ำเสมอ ห้ามใช้ Early Break ตัดจบกลางคันจนหลุดภาพช่วงกลางถึงท้ายคลิป
- การแบ่งและจับเวลาซับไตเติล (Subtitle Sync) ต้องใช้ ร่วมกับตัวตัดประโยคตามธรรมชาติภาษาไทย (split_narration_sentences) โดยตัดข้อความขึ้นบรรทัดใหม่ด้วย \N ใน ASS ห้ามประมาณเวลาด้วยความยาวตัวอักษรแบบหยาบ (len(c) / total_chars) หรือใช้ greedy clamping จนเสียงพูดประโยคถัดไปเริ่มก่อนที่ซับไตเติลจะเปลี่ยน

## 3. Architecture & Formatting
- (Add any specific coding style or architecture rules here)
