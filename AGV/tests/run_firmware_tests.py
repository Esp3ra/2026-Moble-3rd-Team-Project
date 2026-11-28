"""Compile the real firmware logic/HTTP handlers with inert hardware and socket stubs."""

from pathlib import Path
import os
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SKETCH = ROOT / "AgvRouteTest" / "AgvRouteTest.ino"


def section(source, start, end):
    begin = source.index(start)
    return source[begin:source.index(end, begin)]


def main():
    source = SKETCH.read_text(encoding="utf-8-sig")
    data = section(source, "constexpr int MOTOR_RX", "const char CONTROL_PAGE[]")
    logic = section(source, "void sendMotor(", "esp_err_t showControlScript(")
    logic = logic.replace(section(logic, "Sample readLine()", "void publishStatus("), "")
    handlers = section(source, "esp_err_t showControlScript(", "void setup() {")
    sampling = r"""
unsigned int frontReadCount=0;
uint8_t frontMask=0x0c;
Sample sample(uint8_t mask) {
    Sample value;
    value.bits=mask;
    for(int i=0;i<SENSOR_COUNT;++i) {
        if(mask & (1u<<i)) {
            value.count++;
            value.positionSum+=2*i-(SENSOR_COUNT-1);
        }
    }
    return value;
}
Sample readLine() {++frontReadCount;return sample(frontMask);}
"""
    text = (
        (ROOT / "tests/firmware_stubs.h").read_text(encoding="utf-8")
        + data + sampling + logic + handlers
        + source[source.index("void loop() {"):]
        + (ROOT / "tests/firmware_test.cpp").read_text(encoding="utf-8")
    )
    compiler = shutil.which("g++") or shutil.which("clang++") or shutil.which("cl")
    if not compiler:
        raise SystemExit("A C++20 compiler (g++, clang++, or cl) is required for the real route table.")
    with tempfile.TemporaryDirectory(prefix="agv-firmware-test-") as temporary:
        folder = Path(temporary)
        cpp = folder / "firmware_test.cpp"
        cpp.write_text(text, encoding="utf-8")
        executable = folder / ("firmware_test.exe" if os.name == "nt" else "firmware_test")
        if Path(compiler).stem.lower() == "cl":
            arguments = [compiler, "/nologo", "/EHsc", "/utf-8", "/std:c++20",
                         "/I" + str(ROOT / "AgvRouteTest"), str(cpp),
                         "/Fe" + str(executable), "/Fo" + str(folder / "test.obj")]
        else:
            arguments = [compiler, "-std=c++20", "-I", str(ROOT / "AgvRouteTest"),
                         str(cpp), "-o", str(executable)]
        subprocess.run(arguments, check=True, cwd=folder)
        subprocess.run([str(executable)], check=True)


if __name__ == "__main__":
    main()
