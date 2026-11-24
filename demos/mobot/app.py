import os
from html import escape
from pathlib import Path
from string import Template

from flask import Flask, abort, redirect, request
from waitress import serve

from orders import BANKS, CARD_ISSUERS, HEAD_CODES, LEFT_CODES, RIGHT_CODES, connect, create_order_api, initialize_orders
from auth import create_auth_api
from equipment import create_equipment_api
from agv import create_agv_api
from remote_equipment import create_device_api
import json


ROOT = Path(__file__).resolve().parent
TEMPLATES = ROOT / "templates"
STATIC = ROOT / "static"

PAGES = {
    "/": ("home", "홈"),
    "/order": ("order", "주문 구성"),
}
SECTIONS = (
    ("home", "홈"),
    ("company", "기업 소개"),
    ("product", "제품 소개"),
    ("ordering", "주문 방법"),
    ("custom", "맞춤 제작"),
)
SECTION_REDIRECTS = {f"/{section}": f"/#{section}" for section, _ in SECTIONS if section != "home"}

HEADS = (
    ("dual", "듀얼아이 작업용 헤드", "두 개의 렌즈를 배치한 일반 작업용 헤드"),
    ("mono", "모노아이 적외선 설비점검용 헤드", "적외선 렌즈 한 개를 배치한 설비 점검용 헤드"),
)
ARMS = (
    ("grip", "집기팔", "부품을 잡는 U자 집게"),
    ("weld", "용접팔", "용접 위치에 접근하는 짧은 원뿔형 팁"),
    ("fasten", "나사팔", "결속 작업에 사용하는 육각기둥형 팁"),
)
APPLICATIONS = (
    ("transfer", "부품 이송", "U자 집게로 부품을 잡고 다음 공정의 작업 위치로 옮기는 구성입니다.", "U자 집게로 금속 부품을 옮기는 모봇"),
    ("welding", "용접 작업", "원뿔형 팁을 접합부에 대고 금속 부품을 용접하는 구성입니다.", "금속 브래킷을 용접하는 모봇"),
    ("fastening", "볼트 체결", "육각기둥형 팁을 사용해 조립 공정의 체결 작업을 수행하는 구성입니다.", "작업대에서 볼트를 체결하는 모봇"),
    ("inspection", "설비 점검", "모노아이 적외선 헤드로 설비의 발열 상태를 살피는 구성입니다.", "적외선 헤드로 산업용 모터를 점검하는 모봇"),
)


def template(template_name, **values):
    source = (TEMPLATES / f"{template_name}.html").read_text(encoding="utf-8")
    return Template(source).substitute(values)


def navigation(current_path):
    links = []
    for section, label in SECTIONS:
        current = ' aria-current="location"' if current_path == "/" and section == "home" else ""
        links.append(f'<a href="/#{section}" data-section-link="{section}"{current}>{label}</a>')
    return "".join(links)


def application_cards():
    return "".join(
        template("application-card", image=image, title=title, detail=detail, alt=alt)
        for image, title, detail, alt in APPLICATIONS
    )


def module_icon(module_id, is_head):
    crop = "156 16 108 94" if is_head else "52 109 115 210"
    return f'<svg class="module-icon" viewBox="{crop}" aria-hidden="true"><use href="#{module_id}"/></svg>'


def module_cards(modules, is_head):
    return "".join(
        template("module-card", name=name, detail=detail, icon=module_icon(module_id, is_head))
        for module_id, name, detail in modules
    )


def slot_options(slot, label, modules):
    options = []
    for module_id, name, _ in modules:
        icon = module_icon(module_id, slot == "head")
        code={"head":HEAD_CODES,"left":LEFT_CODES,"right":RIGHT_CODES}[slot][module_id]
        options.append(template("order-option", slot=slot, value=code, symbol=module_id, name=f"{name} ({code})", icon=icon))
    return template("order-slot", slot=slot, label=label, options="".join(options))


def render(path, *, live=False):
    name, label = PAGES[path]
    title = "MOBOT | 산업용 모듈형 휴머노이드" if path == "/" else f"{label} | MOBOT"
    symbols = (STATIC / "images" / "assembly.svg").read_text(encoding="utf-8")
    if name == "home":
        product = template(
            "product",
            applications=application_cards(),
            heads=module_cards(HEADS, True),
            arms=module_cards(ARMS, False),
        )
        ordering = template(
            "ordering",
            last_step="주문 정보를 확인하고 접수합니다." if live else "조립 모습을 확인합니다.",
            last_detail=(
                "결제 수단을 선택하고 주문 정보 확인을 누르세요. 확인 창에서 정보를 검토한 뒤 주문을 접수할 수 있습니다."
                if live else "선택한 모듈이 본체에 표시됩니다. 주문 정보 확인을 누르면 세 부위의 선택 내역을 볼 수 있습니다."
            ),
            ordering_note="" if live else '<p class="order-note">모듈 구성과 확인을 체험하는 데모입니다.</p>',
        )
        content = template("home") + template("company") + product + ordering + template("custom")
        script = "sections"
    else:
        options = "".join((
            slot_options("head", "헤드 모듈", HEADS),
            slot_options("left", "왼팔 모듈", ARMS),
            slot_options("right", "오른팔 모듈", ARMS),
        ))
        content = template(
            "order",
            options=options,
            payment=template(
                "order-payment",
                banks="".join(f'<option value="{code}">{name}</option>' for code, name in BANKS.items()),
                issuers="".join(f'<option value="{code}">{name}</option>' for code, name in CARD_ISSUERS.items()),
            ) if live else "",
            order_note=(
                "선택한 결제 옵션을 주문에 기록합니다. 결제 승인과 송금은 이루어지지 않습니다."
                if live else "입력한 정보와 모듈 구성을 화면에서 확인하는 주문 데모입니다."
            ),
            submission_status='<p id="submission-status" class="order-note" role="status"></p>' if live else "",
            submit_order=template("order-submit") if live else "",
            close_label="수정하기" if live else "닫기",
        )
        script = "order"

    return template(
        "layout",
        title=escape(title),
        navigation=navigation(path),
        order_current=' aria-current="page"' if name == "order" else "",
        content=symbols + content,
        cta=template("cta") if name == "home" else "",
        script=f'<script src="/static/{script}.js" defer></script>',
    )


def create_app():
    app = Flask(__name__)
    app.config.update(
        ORDER_DB=Path(os.environ.get("MOBOT_DB", ROOT / "instance" / "mobot.sqlite3")),
        ADMIN_TOKEN=os.environ.get("MOBOT_ADMIN_TOKEN", ""),
        MAX_CONTENT_LENGTH=8192,
        UNO_DEMO_ROOT=ROOT / 'instance' / 'uno-demo',
        UNO_DEMO_QRS=ROOT / 'pallet_qrs',
        EQUIPMENT_MODE='DEMO',
        REMOTE_DEVICE_ID='uno-pi',
    )
    settings=app.config['ORDER_DB'].parent/'remote-equipment.json'
    if settings.exists():
        config=json.loads(settings.read_text(encoding='utf-8'))
        app.config.update(EQUIPMENT_MODE=config.get('mode','DEMO'),REMOTE_DEVICE_ID=config.get('device_id','uno-pi'))
    app.config['EQUIPMENT_MODE']=os.environ.get('MOBOT_EQUIPMENT_MODE',app.config['EQUIPMENT_MODE'])
    if app.config['EQUIPMENT_MODE'] not in {'DEMO','REMOTE'}:raise ValueError('MOBOT_EQUIPMENT_MODE는 DEMO 또는 REMOTE여야 합니다.')
    initialize_orders(app.config["ORDER_DB"])
    app.register_blueprint(create_order_api(HEADS, ARMS))
    app.register_blueprint(create_auth_api(connect))
    app.register_blueprint(create_equipment_api())
    app.register_blueprint(create_agv_api())
    app.register_blueprint(create_device_api())
    from factory_v1.runtime import install
    app.config['LEGACY_API_ENABLED']=os.environ.get('MOBOT_LEGACY_API_ENABLED','0')=='1'
    install(app)
    from wireless_control import install as install_wireless_test
    install_wireless_test(app)

    @app.before_request
    def limits():
        request.max_content_length = 512000 if request.endpoint=='devices.frame' else (65536 if request.endpoint=='devices.poll' else 8192)

    @app.route("/")
    @app.route("/order")
    def page():
        page=render(request.path, live=True)
        if app.config['V1_MODE']=='TRANSPORT_TEST':
            banner='<aside role="alert" style="padding:16px;background:#fff0c2;color:#733800;text-align:center;font-weight:bold">운송 시험 전용 · 로봇팔 조립·YOLO 검사 생략 · 시험 DB 사용 · 제품 합격 판정 없음</aside>'
            page=page.replace('<main id="main"',banner+'<main id="main"',1)
        return page

    @app.get("/<section>")
    def section_redirect(section):
        destination = SECTION_REDIRECTS.get(f"/{section}")
        if destination is None:
            abort(404)
        return redirect(destination)

    return app


if __name__ == "__main__":
    app = create_app()
    host = os.environ.get("MOBOT_HOST", "127.0.0.1")
    port = int(os.environ.get("MOBOT_PORT", "8000"))
    print(f"MOBOT  http://{host}:{port}", flush=True)
    serve(app, host=host, port=port, threads=8, connection_limit=512,
          channel_timeout=15, cleanup_interval=5, max_request_body_size=512000,
          trusted_proxy="127.0.0.1", trusted_proxy_count=1,
          trusted_proxy_headers={"x-forwarded-for"})
