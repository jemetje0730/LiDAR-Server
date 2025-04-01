import asyncio
import time
import uvicorn
import threading
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import listener
import socket  

app = FastAPI()

# ✅ CORS 설정 추가
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 모든 도메인 허용 (보안이 필요하면 특정 도메인만 허용)
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ✅ XOR 체크섬
def xor_checksum(data: bytes) -> int:
    result = 0
    for b in data:
        result ^= b
    return result

# ✅ 패킷 생성기
def make_packet(command: bytes, data: bytes) -> bytes:
    header = b'\xfa'
    productline = b'\x06'
    device_id = b'\xd0'
    datalength = len(data).to_bytes(2, byteorder='big')
    payload = header + productline + device_id + command + datalength + data
    checksum = xor_checksum(payload).to_bytes(1, byteorder='big')
    return payload + checksum


class ChannelRequest(BaseModel):
    output_channel: int

# ✅ WebSocket 연결을 관리할 리스트
active_connections = set()

# ✅ 요청 데이터 모델
class LidarConfig(BaseModel):
    ip: str
    port: int

@app.get("/")
def root():
    return RedirectResponse(url="/lidar_data")

@app.post("/connect")
def connect_device(config: LidarConfig):
    """ 사용자가 입력한 IP가 활성 IP 목록에 있는지 확인 후 리스너 실행 """
    active_ips = listener.find_active_ips(config.port)

    if config.ip not in active_ips:
        return {"success": False, "message": "❌ 입력한 IP가 활성화되지 않음"}

    # ✅ 선택된 IP에 대해 LiDAR 리스너 실행
    success = listener.configure_lidar_listener(config.ip, config.port)
    if not success:
        return {"success": False, "message": "❌ LiDAR 리스너 실행 실패"}

    return {"success": True, "message": f"✅ LiDAR 리스너 실행 완료: {config.ip}:{config.port}"}

@app.post("/set_output_channel")
async def set_output_channel(req: ChannelRequest):
    try:
        # 1️⃣ 채널 설정 패킷 생성
        set_packet = make_packet(b'\xcf\x30', req.output_channel.to_bytes(1, 'big'))
        print("✅ 채널 설정 패킷:", set_packet.hex())

        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            except:
                pass

            sock.bind(("192.168.0.100", 5000))
            sock.settimeout(3)

            # 2️⃣ cf30 패킷 전송
            sock.sendto(set_packet, ("192.168.0.200", 5000))
            print("📤 cf30 전송 완료. 응답 대기 중...")

            try:
                # 3️⃣ cf31 응답 수신
                response1, _ = sock.recvfrom(2048)
                print("📩 cf31 응답 수신:", response1.hex())

                # ✅ cf31 이 맞는지 확인
                if len(response1) >= 7 and response1[3] == 0xcf and response1[4] == 0x31:
                    # ⏱️ 살짝 대기 (라이다가 cf30 처리할 시간)
                    print("✅ cf31 응답이 정상적으로 수신되었습니다.")
                    time.sleep(0.3)

                    # 4️⃣ ed 패킷 생성 후 전송 (형식에 맞춰서 ed 패킷 생성)
                    ed_packet = make_packet(b'\xcf\x10', b'\xed\x1f')
                    print(f"➡️ ED 요청 전송: {ed_packet.hex()}")
                    sock.sendto(ed_packet, ("192.168.0.200", 5000))

                    try:
                        # 5️⃣ cf11 응답 수신
                        sock.settimeout(5)  # 타임아웃을 길게 설정
                        response2, _ = sock.recvfrom(2048)
                        print("📥 cf11 응답 수신:", response2.hex())

                        # ✅ 응답이 올바른지 체크
                        if len(response2) >= 7 and response2[3] == 0xcf and response2[4] == 0x11:
                            print("✅ cf11 응답 처리 완료")
                            return {
                                "channel_setting": response1.hex(),
                                "config_request": ed_packet.hex(),
                                "config_response": response2.hex()
                            }
                        else:
                            return {"error": "❌ cf11 응답 형식이 올바르지 않음"}

                    except socket.timeout:
                        print("❌ cf11 응답 timeout")
                        return {"error": "❌ cf11 응답 timeout"}

                else:
                    return {"error": "❌ cf31 응답이 아님"}

            except socket.timeout:
                return {"error": "❌ cf31 응답 timeout"}

    except Exception as e:
        print("❌ 예외 발생:", str(e))
        return {"error": str(e)}

class AreaDataRequest(BaseModel):
    area_data: int

@app.post("/set_area_data")
def set_area_data(request: AreaDataRequest):
    area_data = request.area_data
    if not (0 <= area_data <= 255):
        raise HTTPException(status_code=400, detail="Size must be between 0 and 255")
    
    hex_size = f"{area_data:02x}"
    payload = make_packet(b'\xcf\x50', bytes.fromhex(hex_size))

    print("📤 Area Data 설정 패킷:", payload.hex())

    # ✅ LiDAR로 UDP 패킷 전송
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except:
            pass

        sock.bind(("192.168.0.100", 5000))  # 로컬 IP와 포트 바인딩
        sock.settimeout(3)

        # 📤 패킷 전송
        sock.sendto(payload, ("192.168.0.200", 5000))
        print("📤 Area Data 패킷 전송 완료. 응답 대기 중...")

        try:
            # ✅ LiDAR의 응답 수신
            response, _ = sock.recvfrom(2048)
            print("📥 LiDAR 응답 수신:", response.hex())

            # 응답이 정상적인지 체크
            if len(response) >= 7 and response[3] == 0xcf and response[4] == 0x51:
                print("✅ Area Data 설정 성공!")
                return {
                    "message": "Area size set successfully",
                    "payload": payload.hex(),
                    "response": response.hex()
                }
            else:
                return {"error": "❌ 예상치 못한 응답"}

        except socket.timeout:
            print("❌ LiDAR 응답 timeout")
            return {"error": "❌ LiDAR 응답 timeout"}

class DistanceRangeRequest(BaseModel):
    min_distance: int
    max_distance: int

@app.post("/set_distance_range")
def set_distance_range(req: DistanceRangeRequest):
    print(f"🔹 [DEBUG] Received request: min={req.min_distance}, max={req.max_distance}")

    if not (0 <= req.min_distance <= 50 and 0 <= req.max_distance <= 50):
        print("❌ 거리 범위 오류")
        raise HTTPException(status_code=400, detail="Distance range must be between 0 and 50 meters")

    min_hex = req.min_distance.to_bytes(1, 'big')
    max_hex = req.max_distance.to_bytes(1, 'big')

    payload = make_packet(b'\xcf\x80', min_hex + max_hex)
    print(f"📤 [DEBUG] Sending packet: {payload.hex()}")

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except:
            pass

        sock.bind(("192.168.0.100", 5000))
        sock.settimeout(3)

        sock.sendto(payload, ("192.168.0.200", 5000))
        print("📤 Distance Range 패킷 전송 완료. 응답 대기 중...")

        try:
            response, _ = sock.recvfrom(2048)
            print(f"📥 [DEBUG] Received response: {response.hex()}")

            if len(response) >= 7 and response[3] == 0xcf and response[4] == 0x81:
                print("✅ Distance Range 설정 성공!")
                return {"message": "Distance range set successfully", "payload": payload.hex(), "response": response.hex()}
            else:
                return {"error": "❌ 예상치 못한 응답"}

        except socket.timeout:
            print("❌ LiDAR 응답 timeout")
            return {"error": "❌ LiDAR 응답 timeout"}

# ✅ Self Check 요청 모델
class SelfCheckRequest(BaseModel):
    state: str  # "high" 또는 "low"

@app.post("/set_self_check")
def set_self_check(req: SelfCheckRequest):
    state = req.state.lower()  # 대소문자 변환

    if state not in ["high", "low"]:
        raise HTTPException(status_code=400, detail="State must be 'high' or 'low'")

    # ✅ "high" -> 01, "low" -> 00 변환
    state_value = b'\x01' if state == "high" else b'\x00'
    
    # ✅ 패킷 생성 (cfb0 명령어 사용)
    payload = make_packet(b'\xcf\xb0', state_value)
    print(f"📤 Sending packet: {payload.hex()}")

    # ✅ LiDAR로 UDP 패킷 전송
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except:
            pass

        sock.bind(("192.168.0.100", 5000))  # 로컬 IP와 포트 바인딩
        sock.settimeout(3)

        # 📤 패킷 전송
        sock.sendto(payload, ("192.168.0.200", 5000))
        print("📤 Self Check 패킷 전송 완료. 응답 대기 중...")

        try:
            # ✅ LiDAR의 응답 수신
            response, _ = sock.recvfrom(2048)
            print("📥 LiDAR 응답 수신:", response.hex())

            # ✅ 응답이 정상적인지 체크
            if len(response) >= 7 and response[3] == 0xcf and response[4] == 0xb1:
                print("✅ Self Check 설정 성공!")
                return {
                    "message": "Self Check set successfully",
                    "payload": payload.hex(),
                    "response": response.hex()
                }
            else:
                return {"error": "❌ 예상치 못한 응답"}

        except socket.timeout:
            print("❌ LiDAR 응답 timeout")
            return {"error": "❌ LiDAR 응답 timeout"}
        
# ✅ Pulse Data 요청 모델
class PulseDataRequest(BaseModel):
    value: int  # 0~1000 (100 단위)

@app.post("/set_pulse_data")
def set_pulse_data(req: PulseDataRequest):
    pulse_value = req.value

    # ✅ 유효한 값인지 체크 (100ms 단위인지, 0~1000 범위인지)
    if pulse_value % 100 != 0 or not (0 <= pulse_value <= 1000):
        raise HTTPException(status_code=400, detail="Value must be in range [0, 1000] with step 100")

    # ✅ 100ms 단위 값을 HEX 변환 (400ms -> 0x04)
    hex_value = (pulse_value // 100).to_bytes(1, byteorder="big")

    # ✅ 패킷 생성 (cf60 명령어 사용)
    payload = make_packet(b'\xcf\x60', hex_value)
    print(f"📤 Sending Pulse Data packet: {payload.hex()} (value={pulse_value}ms)")

    # ✅ LiDAR로 UDP 패킷 전송
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except:
            pass

        sock.bind(("192.168.0.100", 5000))  # 로컬 IP와 포트 바인딩
        sock.settimeout(3)

        # 📤 패킷 전송
        sock.sendto(payload, ("192.168.0.200", 5000))
        print("📤 Pulse Data 패킷 전송 완료. 응답 대기 중...")

        try:
            # ✅ LiDAR의 응답 수신 (cf61 응답)
            response, _ = sock.recvfrom(2048)
            print(f"📥 Received LiDAR response: {response.hex()}")

            # ✅ 응답이 정상적인지 체크
            if len(response) >= 7 and response[3] == 0xcf and response[4] == 0x61:
                print("✅ Pulse Data 설정 성공!")
                return {
                    "message": "Pulse Data set successfully",
                    "sent_payload": payload.hex(),
                    "received_response": response.hex()
                }
            else:
                print("❌ 예상치 못한 응답")
                return {"error": "❌ 예상치 못한 응답", "response": response.hex()}

        except socket.timeout:
            print("❌ LiDAR 응답 timeout")
            return {"error": "❌ LiDAR 응답 timeout"}

# ✅ Angle Data 요청 모델
class AngleDataRequest(BaseModel):
    min_angle: int  # 최소 각도 (0~100)
    max_angle: int  # 최대 각도 (0~100)

@app.post("/set_angle_data")
def set_angle_data(req: AngleDataRequest):
    min_angle = req.min_angle
    max_angle = req.max_angle

    # ✅ 유효한 값인지 체크 (0~100 범위 확인)
    if not (0 <= min_angle <= 100 and 0 <= max_angle <= 100):
        raise HTTPException(status_code=400, detail="Angles must be in range [0, 100]")

    if min_angle > max_angle:
        raise HTTPException(status_code=400, detail="Min Angle must be less than or equal to Max Angle")

    # ✅ 1바이트 HEX 변환 (0~100 -> 0x00 ~ 0x64)
    min_angle_hex = min_angle.to_bytes(1, byteorder="big")
    max_angle_hex = max_angle.to_bytes(1, byteorder="big")

    # ✅ 패킷 생성 (cf80 명령어 사용)
    payload = make_packet(b'\xcf\x80', min_angle_hex + max_angle_hex)
    print(f"📤 Sending Angle Data packet: {payload.hex()} (min={min_angle}°, max={max_angle}°)")

    # ✅ LiDAR로 UDP 패킷 전송
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except:
            pass

        sock.bind(("192.168.0.100", 5000))  # 로컬 IP와 포트 바인딩
        sock.settimeout(3)

        # 📤 패킷 전송
        sock.sendto(payload, ("192.168.0.200", 5000))
        print("📤 Angle Data 패킷 전송 완료. 응답 대기 중...")

        try:
            # ✅ LiDAR의 응답 수신 (cf81 응답)
            response, _ = sock.recvfrom(2048)
            print(f"📥 Received LiDAR response: {response.hex()}")

            # ✅ 응답이 정상적인지 체크
            if len(response) >= 7 and response[3] == 0xcf and response[4] == 0x81:
                print("✅ Angle Data 설정 성공!")
                return {
                    "message": "Angle Data set successfully",
                    "sent_payload": payload.hex(),
                    "received_response": response.hex()
                }
            else:
                print("❌ 예상치 못한 응답")
                return {"error": "❌ 예상치 못한 응답", "response": response.hex()}

        except socket.timeout:
            print("❌ LiDAR 응답 timeout")
            return {"error": "❌ LiDAR 응답 timeout"}

@app.websocket("/ws/lidar")
async def websocket_lidar(websocket: WebSocket):
    await websocket.accept()
    print("✅ WebSocket 클라이언트 연결됨")

    try:
        while True:
            lidar_data = listener.get_latest_data()
            response = {
                "lidar_data": lidar_data.get("lidar", {}),
                "distances": lidar_data.get("distances", {})
            }
            await websocket.send_json(response)
            await asyncio.sleep(0.1)  # ✅ 실시간 갱신 속도 조절
    except WebSocketDisconnect:
        print("❌ WebSocket 클라이언트 연결 종료됨")

@app.get("/lidar_data")
def get_lidar_data():
    """ 최신 LiDAR 데이터를 반환 (모든 채널 포함) """
    lidar_data = listener.get_latest_data().get("lidar_data", {})

    if lidar_data:
        formatted_data = []
        for channel, points in lidar_data.items():  # ✅ 모든 채널 데이터 가져오기
            for p in points:
                formatted_data.append({
                    "channel": channel,  # ✅ 채널 번호 추가
                    "x": p["x"],
                    "y": p["y"],
                    "z": p["z"],
                    "distance": p["distance"]
                })

        return {"latest_lidar_xyz_points": formatted_data}  # ✅ 모든 데이터 반환

    return {"message": "No data available"}


@app.get("/lidar/config")
def get_lidar_config():
    """ 최신 LiDAR 데이터 반환 (header, productline, id, command, datalength, checksum) """
    lidar_data = listener.latest_data.get("lidar", {})

    if not lidar_data:  # 🚨 데이터가 없으면 404 방지
        print("❌ [Error] No LiDAR data available in listener.py")
        raise HTTPException(status_code=404, detail="No LiDAR data available")

    print(f"✅ [Success] LiDAR config returned: {lidar_data}")  # 🚀 성공 로그 추가
    return lidar_data  # ✅ 바로 반환 (이미 hex 변환된 데이터)

def run_server():
    print("✅ 서버 실행 중...")
    uvicorn.run(app, host="0.0.0.0", port=8000)

if __name__ == "__main__":
    server_thread = threading.Thread(target=run_server, daemon=True)
    server_thread.start()
    input("Press Enter to stop the server...\n")
