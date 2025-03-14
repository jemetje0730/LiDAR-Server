import asyncio
import requests as req
import uvicorn
import threading
from fastapi import FastAPI, HTTPException, WebSocket
from fastapi.responses import RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import listener

app = FastAPI()

# ✅ CORS 설정 추가
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 모든 도메인 허용 (보안이 필요하면 특정 도메인만 지정)
    allow_credentials=True,
    allow_methods=["*"],  # 모든 HTTP 메서드 허용 (GET, POST 등)
    allow_headers=["*"],  # 모든 헤더 허용
)

# ✅ Cloud Point 데이터 API 주소
CLOUD_POINT_API_URL = "http://127.0.0.1:18290/v1/lidar/cloud-point?index=0"

# ✅ WebSocket 클라이언트 목록 저장
connected_clients = []

# ✅ 요청 데이터 모델
class LidarConfig(BaseModel):
    ip: str
    port: int

@app.get("/")
def root():
    return RedirectResponse(url="/lidar_data")

@app.post("/setup_lidar")
def setup_lidar(config: LidarConfig):
    active_ips = listener.find_active_ips(config.port)  # ✅ 여러 개의 IP 가져오기

    if not active_ips:
        raise HTTPException(status_code=400, detail=f"❌ 포트 {config.port}에서 활성화된 장치를 찾을 수 없음")

    if config.ip not in active_ips:
        raise HTTPException(status_code=400, detail=f"❌ 입력한 IP({config.ip})가 멀티캐스트 그룹에 존재하지 않습니다. 감지된 IP: {active_ips}")

    # ✅ 선택된 IP에 대해 LiDAR 리스너 실행
    success = listener.configure_lidar_listener(config.ip, config.port)
    if not success:
        raise HTTPException(status_code=400, detail="❌ LiDAR 설정 실패")

    return {"status": "success", "message": f"✅ LiDAR 설정 완료: {config.ip}:{config.port}"}


@app.get("/lidar_data")
def get_lidar_data():
    """ 최신 LiDAR 데이터를 반환 """
    lidar_data = listener.get_latest_data().get("lidar_data", [])
    if not lidar_data:
        raise HTTPException(status_code=404, detail="No LiDAR data available")
    return {"latest_lidar_xyz_points": lidar_data}

@app.get("/cloud_point_data")
def get_cloud_point_data():
    """ 최신 Cloud Point 데이터를 반환하는 API """
    return fetch_cloud_point_data()

@app.websocket("/ws/cloud_point")
async def websocket_endpoint(websocket: WebSocket):
    """ WebSocket을 통해 실시간 Cloud Point 데이터를 전송 """
    await websocket.accept()
    connected_clients.append(websocket)
    print("✅ WebSocket 클라이언트 연결됨")

    try:
        # ✅ 연결된 즉시 최신 데이터 한 번 전송
        data = fetch_cloud_point_data()
        if data:
            await websocket.send_json(data)

        await websocket.receive_text()  # 클라이언트가 연결을 유지하도록 함
    except Exception as e:
        print(f"❌ WebSocket 연결 끊김: {str(e)}")
    finally:
        connected_clients.remove(websocket)

# ✅ 최신 Cloud Point 데이터 가져오기
def fetch_cloud_point_data():
    """ 최신 Cloud Point 데이터를 가져오는 함수 """
    try:
        response = req.get(CLOUD_POINT_API_URL, timeout=2)
        response.raise_for_status()
        data = response.json()

        coord_data = data.get("Res", {}).get("Data", [{}])[0].get("CoordData", [])
        formatted_data = [
            {"x": entry["X"], "y": entry["Y"], "z": entry["Z"], "distance": round(entry["Distance"], 2)}
            for entry in coord_data
        ]
        return {"latest_cloud_point_xyz_points": formatted_data}

    except req.exceptions.RequestException as e:
        print(f"❌ Error fetching cloud point data: {str(e)}")
        return None

async def broadcast_cloud_point_data():
    while True:
        if connected_clients:
            data = fetch_cloud_point_data()
            if data:
                for client in connected_clients:
                    try:
                        await client.send_json(data)
                    except Exception as e:
                        print(f"❌ WebSocket 전송 오류: {str(e)}")
                        connected_clients.remove(client)
        await asyncio.sleep(0.3)  # 0.3초마다 갱신


async def main():
    print("✅ 서버 실행 중...")

    # 🚀 WebSocket 데이터 지속 전송
    asyncio.create_task(broadcast_cloud_point_data())

    # 🚀 FastAPI 서버 실행
    config = uvicorn.Config(app, host="0.0.0.0", port=8000)
    server = uvicorn.Server(config)
    await server.serve()  # ✅ 비동기 실행

def run_server():
    asyncio.run(main())  # ✅ asyncio 이벤트 루프에서 실행

# ✅ 서버 실행 (스레드에서 실행)
if __name__ == "__main__":
    server_thread = threading.Thread(target=run_server, daemon=True)
    server_thread.start()
    input("Press Enter to stop the server...\n")