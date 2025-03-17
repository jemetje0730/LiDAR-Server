import asyncio
import requests as req
import uvicorn
import threading
from fastapi import FastAPI, HTTPException, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import listener

app = FastAPI()

# ✅ CORS 설정
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ✅ Cloud Point 데이터 API 주소
CLOUD_POINT_API_URL = "http://127.0.0.1:18290/v1/lidar/cloud-point?index=0"

# ✅ WebSocket 클라이언트 목록 저장
connected_clients = []
last_cloud_data = []

# ✅ 요청 데이터 모델
class DeviceConfig(BaseModel):
    ip: str
    port: int

@app.post("/connect")
def connect_device(config: DeviceConfig):
    """ 사용자가 입력한 IP가 활성 IP 목록에 있는지 확인 """
    active_ips = listener.find_active_ips(config.port)

    if config.ip in active_ips:
        return {"success": True}
    return {"success": False, "message": "IP가 활성화되지 않음"}

@app.websocket("/ws/cloud_point")
async def websocket_endpoint(websocket: WebSocket):
    """ WebSocket을 통해 실시간 Cloud Point 데이터 전송 """
    await websocket.accept()
    connected_clients.append(websocket)
    print("✅ WebSocket 클라이언트 연결됨")

    try:
        while True:
            new_data = fetch_cloud_point_data()
            if new_data:
                await websocket.send_json(new_data)
            await asyncio.sleep(0.1)
    except Exception:
        pass
    finally:
        connected_clients.remove(websocket)

def fetch_cloud_point_data():
    """ 최신 Cloud Point 데이터를 가져오는 함수 (변경된 데이터만 필터링) """
    try:
        response = req.get(CLOUD_POINT_API_URL, timeout=2)
        response.raise_for_status()
        data = response.json()

        coord_data = data.get("Res", {}).get("Data", [{}])[0].get("CoordData", [])

        new_data = [
            {"x": entry["X"], "y": entry["Y"], "z": entry["Z"], "distance": round(entry["Distance"], 2)}
            for entry in coord_data
        ]

        # 변경된 데이터만 반영 (이전 데이터 비교)
        global last_cloud_data
        if last_cloud_data == new_data:
            return None  # 변경 사항 없으면 전송 안 함

        last_cloud_data = new_data
        return {"latest_cloud_point_xyz_points": new_data}

    except req.exceptions.RequestException as e:
        print(f"❌ Error fetching cloud point data: {str(e)}")
        return None

async def main():
    print("✅ 서버 실행 중...")
    asyncio.create_task(websocket_broadcast())
    config = uvicorn.Config(app, host="0.0.0.0", port=8000)
    server = uvicorn.Server(config)
    await server.serve()

async def websocket_broadcast():
    """ 연결된 모든 WebSocket 클라이언트에 데이터 전송 """
    while True:
        if connected_clients:
            new_data = fetch_cloud_point_data()
            if new_data:
                for client in connected_clients:
                    try:
                        await client.send_json(new_data)
                    except:
                        connected_clients.remove(client)
        await asyncio.sleep(0.1)

if __name__ == "__main__":
    threading.Thread(target=lambda: asyncio.run(main()), daemon=True).start()
    input("Press Enter to stop the server...\n")
