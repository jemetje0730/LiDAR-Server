import asyncio
import struct
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

# ✅ 패킷 생성
def make_packet(command: bytes, data: bytes) -> bytes:
    payload = b'\xfa\x06\xd0' + command + len(data).to_bytes(2, 'big') + data
    return payload + xor_checksum(payload).to_bytes(1, 'big')

# ✅ ED 패킷 즉시 전송 함수 (멀티캐스트 수신 대응)
def send_existing_data_request():
    print("✅ send_existing_data_request() 실행됨")
    ed_packet = make_packet(b'\xcf\x10', b'\xed')

    MULTICAST_GROUP = "224.0.0.5"
    LOCAL_IP = "192.168.0.100"  # 로컬 인터페이스 IP (변경 가능)
    UDP_PORT = 5000  # 항상 5000 포트 사용

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        # 모든 네트워크 인터페이스에서 멀티캐스트 수신하도록 설정
        sock.bind(("0.0.0.0", UDP_PORT))  

        # ✅ 멀티캐스트 그룹 가입 (224.0.0.5)
        mreq = struct.pack("4s4s", socket.inet_aton(MULTICAST_GROUP), socket.inet_aton(LOCAL_IP))
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)

        # ✅ ED 패킷 전송 (항상 5000 포트에서 전송)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as send_sock:
            send_sock.bind((LOCAL_IP, UDP_PORT))  # ✅ 송신 포트 고정
            send_sock.sendto(ed_packet, ("192.168.0.200", UDP_PORT))
            print(f"📤 ED Packet Sent from {LOCAL_IP}:{UDP_PORT} -> 192.168.0.200:{UDP_PORT}")

        try:
            response, _ = sock.recvfrom(2048)
            print(f"📥 Received ED response: {response.hex()}")
            return response.hex()
        except socket.timeout:
            print("❌ ED Response timeout")

    return None


def send_packet(payload: bytes, expected_cmd: bytes):
    print(f"📤 Sending packet: {payload.hex()}")

    LOCAL_IP = "192.168.0.100"  # 본인의 IP (변경 가능)
    UDP_PORT = 5000  # 항상 5000 포트 사용
    MULTICAST_GROUP = "224.0.0.5"

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except:
            pass

        # ✅ 모든 네트워크 인터페이스에서 수신 가능하도록 바인딩
        sock.bind(("0.0.0.0", UDP_PORT))

        # ✅ 멀티캐스트 그룹 가입
        mreq = struct.pack("4s4s", socket.inet_aton(MULTICAST_GROUP), socket.inet_aton(LOCAL_IP))
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)

        # ✅ 패킷 전송 (항상 5000 포트에서 전송)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as send_sock:
            send_sock.bind((LOCAL_IP, UDP_PORT))  # ✅ 송신 포트 고정
            send_sock.sendto(payload, ("192.168.0.200", UDP_PORT))
            print(f"📤 Packet Sent from {LOCAL_IP}:{UDP_PORT} -> 192.168.0.200:{UDP_PORT}")

        try:
            response, _ = sock.recvfrom(2048)
            print(f"📥 Received response: {response.hex()}")

            if len(response) >= 7 and response[3:5] == expected_cmd:
                print("✅ Command executed successfully!")
                return {"message": "Success", "sent": payload.hex(), "received": response.hex()}
            else:
                return {"error": "Unexpected response", "received": response.hex()}
        except socket.timeout:
            print("❌ Response timeout")
            return {"error": "Response timeout"}


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
def set_output_channel(req: ChannelRequest):
    response = send_packet(make_packet(b'\xcf\x30', req.output_channel.to_bytes(1, 'big')), b'\xcf\x31')
    send_existing_data_request()
    return response


class AreaDataRequest(BaseModel):
    area_data: int

@app.post("/set_area_data")
def set_area_data(req: AreaDataRequest):
    if not (0 <= req.area_data <= 255):
        raise HTTPException(status_code=400, detail="Size must be between 0 and 255")
    response = send_packet(make_packet(b'\xcf\x50', req.area_data.to_bytes(1, 'big')), b'\xcf\x51')
    send_existing_data_request()
    return response

class DistanceRangeRequest(BaseModel):
    min_distance: int
    max_distance: int

@app.post("/set_distance_range")
def set_distance_range(req: DistanceRangeRequest):
    if not (0 <= req.min_distance <= 50 and 0 <= req.max_distance <= 50):
        raise HTTPException(status_code=400, detail="Distance range must be between 0 and 50 meters")
    response = send_packet(make_packet(b'\xcf\x80', req.min_distance.to_bytes(1, 'big') + req.max_distance.to_bytes(1, 'big')), b'\xcf\x81')
    send_existing_data_request()
    return response

class SelfCheckRequest(BaseModel):
    state: str  # "high" 또는 "low"

@app.post("/set_self_check")
def set_self_check(req: SelfCheckRequest):
    if req.state.lower() not in ["high", "low"]:
        raise HTTPException(status_code=400, detail="State must be 'high' or 'low'")
    response = send_packet(make_packet(b'\xcf\xb0', b'\x01' if req.state.lower() == "high" else b'\x00'), b'\xcf\xb1')
    send_existing_data_request()
    return response

class PulseDataRequest(BaseModel):
    value: int  # 0~1000 (100 단위)

@app.post("/set_pulse_data")
def set_pulse_data(req: PulseDataRequest):
    if req.value % 100 != 0 or not (0 <= req.value <= 1000):
        raise HTTPException(status_code=400, detail="Value must be in range [0, 1000] with step 100")
    response = send_packet(make_packet(b'\xcf\x60', (req.value // 100).to_bytes(1, 'big')), b'\xcf\x61')
    send_existing_data_request()
    return response

class AngleDataRequest(BaseModel):
    min_angle: int
    max_angle: int

@app.post("/set_angle_data")
def set_angle_data(req: AngleDataRequest):
    if not (0 <= req.min_angle <= 100 and 0 <= req.max_angle <= 100):
        raise HTTPException(status_code=400, detail="Angles must be in range [0, 100]")
    if req.min_angle > req.max_angle:
        raise HTTPException(status_code=400, detail="Min Angle must be less than or equal to Max Angle")
    response = send_packet(make_packet(b'\xcf\x80', req.min_angle.to_bytes(1, 'big') + req.max_angle.to_bytes(1, 'big')), b'\xcf\x81')
    send_existing_data_request()
    return response

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
