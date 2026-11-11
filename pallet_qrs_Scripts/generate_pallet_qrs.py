import os
import qrcode
from PIL import Image, ImageDraw
import cv2

# 1. 스크립트 기준 동적 상대 경로 설정
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# 로고 파일 탐색 (1순위: 스크립트 폴더 내, 2순위: 상위 db 폴더 내)
logo_candidates = [
    os.path.join(SCRIPT_DIR, "pallet_logo.png"),
    os.path.join(SCRIPT_DIR, "..", "db", "pallet_logo.png"),
    os.path.join(SCRIPT_DIR, "pallet_logo.jpg"),
    os.path.join(SCRIPT_DIR, "..", "db", "pallet_logo.jpg")
]

logo_path = None
for candidate in logo_candidates:
    if os.path.exists(candidate):
        logo_path = os.path.abspath(candidate)
        break

if not logo_path:
    raise FileNotFoundError("로고 이미지를 찾을 수 없습니다. (pallet_logo.png 또는 pallet_logo.jpg)")

output_dir = os.path.join(SCRIPT_DIR, 'pallet_qrs')
os.makedirs(output_dir, exist_ok=True)

pallet_ids = ['PALLET-01', 'PALLET-02', 'PALLET-03', 'PALLET-04']

# 2. 누끼/고화질 로고 불러오기
raw_logo = Image.open(logo_path).convert('RGBA')

# 로고 주변에 둥근 흰색 배지(마진)를 두어 QR 모듈과의 광학적 간섭을 원천 차단하고 세련되게 배치
def prepare_logo(size):
    margin = max(5, size // 15)
    inner_size = size - (margin * 2)
    resized_logo = raw_logo.resize((inner_size, inner_size), Image.Resampling.LANCZOS)
    # 둥근 모서리 사각형 흰색 배경 배지
    badge = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(badge)
    radius = int(size * 0.22)
    draw.rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=(255, 255, 255, 255))
    # 누끼 로고 합성
    badge.paste(resized_logo, (margin, margin), resized_logo)
    return badge

detector = cv2.QRCodeDetector()

print(f"[INFO] Using logo: {logo_path}")
print("[INFO] Generating branded QR codes with enhanced Moble 'M' logo...")

for pid in pallet_ids:
    # 버전 4 (33x33 모듈): 에러 복원율 최고 수준(H: 30%)
    qr = qrcode.QRCode(
        version=4,
        error_correction=qrcode.constants.ERROR_CORRECT_H,
        box_size=16,
        border=3,
    )
    qr.add_data(pid)
    qr.make(fit=True)

    qr_img = qr.make_image(fill_color="#0a101d", back_color="#ffffff").convert('RGBA')
    qr_w, qr_h = qr_img.size

    # 로고 크기: 전체의 약 21% (스캔 안전율 최적)
    logo_size = int(qr_w * 0.21)
    logo_badge = prepare_logo(logo_size)

    # 정중앙 배치 (알파 마스크 적용)
    pos = ((qr_w - logo_size) // 2, (qr_h - logo_size) // 2)
    qr_img.paste(logo_badge, pos, logo_badge)

    # 최종 RGB 저장
    final_img = qr_img.convert('RGB')
    file_path = os.path.join(output_dir, f"{pid}.png")
    final_img.save(file_path, "PNG", quality=100)

    # OpenCV 인식 검증
    cv_img = cv2.imread(file_path)
    decoded_text, _, _ = detector.detectAndDecode(cv_img)
    pass_status = "PASS" if decoded_text == pid else "FAIL"
    print(f"[{pass_status}] {pid} -> Decoded: '{decoded_text}' | File: {file_path}")

print("[DONE] Successfully generated all 4 branded QR codes!")
