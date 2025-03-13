import requests as req
import uvicorn
import threading
from fastapi import FastAPI, HTTPException
from fastapi.responses import RedirectResponse
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

# ✅ 요청 데이터 모델
class LidarConfig(BaseModel):
    ip: str
    port: int

@app.get("/")
def root():
    return RedirectResponse(url="/lidar_data")

@app.post("/setup_lidar")
def setup_lidar(config: LidarConfig):
    """ 
    사용자가 입력한 IP와 포트가 멀티캐스트 그룹에 존재하는지 확인 후 LiDAR 리스닝 시작
    """
    active_ip = listener.find_active_ip(config.port)  # 🔍 입력한 포트에서 활성화된 IP 찾기
    if not active_ip:
        raise HTTPException(status_code=400, detail=f"❌ 포트 {config.port}에서 활성화된 장치를 찾을 수 없음")
    
    if active_ip != config.ip:
        raise HTTPException(status_code=400, detail=f"❌ 입력한 IP({config.ip})가 멀티캐스트 그룹에 존재하지 않습니다.")

    # ✅ IP가 일치할 경우, 리스너 실행
    success = listener.configure_lidar_listener(active_ip, config.port)
    if not success:
        raise HTTPException(status_code=400, detail="❌ LiDAR 설정 실패")

    return {"status": "success", "message": f"✅ LiDAR 설정 완료: {active_ip}:{config.port}"}

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
        raise HTTPException(status_code=500, detail=f"Error fetching cloud point data: {str(e)}")

def run_server():
    print("✅ 서버 실행 중...")
    uvicorn.run(app, host="0.0.0.0", port=8000)

if __name__ == "__main__":
    server_thread = threading.Thread(target=run_server, daemon=True)
    server_thread.start()
    input("Press Enter to stop the server...\n")
