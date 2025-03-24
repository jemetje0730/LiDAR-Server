import asyncio
import requests as req
import uvicorn
import threading
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import listener

app = FastAPI()

# ✅ CORS 설정 추가
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 모든 도메인 허용 (보안이 필요하면 특정 도메인만 허용)
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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
