import os
import qrcode
from PIL import Image
import cv2
import numpy as np

logo_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'pallet_logo.jpg')

output_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'pallet_qrs')
os.makedirs(output_dir, exist_ok=True)

pallet_ids = ['PALLET-01', 'PALLET-02', 'PALLET-03', 'PALLET-04', 'PALLET-05', 'PALLET-06']

# 1. 원본 로고 불러오기
raw_logo = Image.open(logo_path).convert('RGBA') if os.path.isfile(logo_path) else None

# 로고 주변에 얇은 흰색 테두리(패딩)를 주어 QR 코드의 흑백 모듈과 시각적/광학적으로 깔끔하게 분리
def prepare_logo(size):
    # 흰색 마진을 위해 로고 크기를 약간 줄임
    margin = max(4, size // 15)
    inner_size = size - (margin * 2)
    resized_logo = raw_logo.resize((inner_size, inner_size), Image.Resampling.LANCZOS)

    badge = Image.new('RGBA', (size, size), (255, 255, 255, 255))
    badge.paste(resized_logo, (margin, margin))
    return badge

detector = cv2.QRCodeDetector()

print("[INFO] Generating QR codes. Optional logo: pallet_logo.jpg next to this script.")

for pid in pallet_ids:
    # 버전 4 (33x33 모듈): 상용 QR처럼 정교하고 밀도 높음
    qr = qrcode.QRCode(
        version=4,
        error_correction=qrcode.constants.ERROR_CORRECT_H, # 최고 복원율 30%
        box_size=16,
        border=3,
    )
    qr.add_data(pid)
    qr.make(fit=True)

    qr_img = qr.make_image(fill_color="#0a101d", back_color="#ffffff").convert('RGBA')
    qr_w, qr_h = qr_img.size

    # 로고 크기: 전체의 약 21% (안전한 스캔 보장 범위)
    if raw_logo is not None:
        logo_size = int(qr_w * 0.22)
        logo_badge = prepare_logo(logo_size)
        pos = ((qr_w - logo_size) // 2, (qr_h - logo_size) // 2)
        qr_img.paste(logo_badge, pos)

    # 저장
    final_img = qr_img.convert('RGB')
    file_path = os.path.join(output_dir, f"{pid}.png")
    final_img.save(file_path, "PNG", quality=100)

    # OpenCV 인식 검증
    cv_img = cv2.imdecode(np.fromfile(file_path, dtype=np.uint8), cv2.IMREAD_COLOR)
    decoded_text, _, _ = detector.detectAndDecode(cv_img)
    pass_status = "PASS" if decoded_text == pid else "FAIL"
    print(f"[{pass_status}] {pid} -> Decoded: '{decoded_text}' | File: {file_path}")
    if decoded_text != pid:
        raise RuntimeError(f"QR verification failed: {pid}. Try a smaller logo.")

print("[DONE] Successfully generated and verified all 6 QR codes!")
