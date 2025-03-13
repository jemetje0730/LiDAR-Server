import requests as req  # requests를 req로 별칭 설정
import uvicorn
import threading
from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel  # ✅ 요청 데이터 검증 추가
import listener

app = FastAPI()  # ✅ FastAPI 인스턴스를 한 번만 선언

# ✅ CORS 설정을 한 번만 추가
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 모든 도메인 허용
    allow_credentials=True,
    allow_methods=["*"],  # 모든 HTTP 메서드 허용
    allow_headers=["*"],  # 모든 헤더 허용
)

# 서버에서 데이터를 가져오기 위한 URL
CLOUD_POINT_API_URL = "http://127.0.0.1:18290/v1/lidar/cloud-point?index=0"

# ✅ 캐싱된 데이터 저장 (중복 요청 방지)
latest_data_cache = {
    "lidar": None,
    "cloud_point": None
}

@app.get("/")
def root():
    return RedirectResponse(url="/lidar_data")


@app.get("/lidar_data")
def get_lidar_data():
    """ 최신 LiDAR 데이터를 반환하는 API """
    lidar_data = listener.get_latest_data().get("lidar_data", [])

    if lidar_data:
        formatted_data = [
            {"x": p["x"], "y": p["y"], "z": p["z"], "distance": p["distance"]}
            for p in lidar_data
        ]
        return {"latest_lidar_xyz_points": formatted_data}

    return {"message": "No data available"}


@app.get("/cloud_point_data")
def get_cloud_point_data():
    """ 최신 Cloud Point 데이터를 반환하는 API """
    try:
        response = req.get(CLOUD_POINT_API_URL, timeout=2)  # ✅ `req.get()`으로 변경
        response.raise_for_status()
        data = response.json()

        coord_data = data.get("Res", {}).get("Data", [{}])[0].get("CoordData", [])

        formatted_data = [
            {"x": entry["X"], "y": entry["Y"], "z": entry["Z"], 
             "distance": round(entry["Distance"], 2)}
            for entry in coord_data
        ]

        if formatted_data and formatted_data != latest_data_cache["cloud_point"]:
            latest_data_cache["cloud_point"] = formatted_data
            return {"latest_cloud_point_xyz_points": formatted_data}

        return {"latest_cloud_point_xyz_points": [], "message": "No new data"}

    except req.exceptions.RequestException as e:  # ✅ 예외 처리 부분도 req.exceptions로 변경
        return {"message": f"Error fetching cloud point data: {str(e)}"}


# ✅ 요청 데이터 모델 정의
class LidarConfig(BaseModel):
    ip: str
    port: int

@app.post("/setup_lidar")
def setup_lidar(config: LidarConfig):
    """사용자가 입력한 IP/포트로 LiDAR 소켓을 설정하고 데이터 수신 시작"""
    success = listener.configure_lidar_listener(config.ip, config.port)
    if success:
        return {"status": "success", "message": f"✅ LiDAR 설정 완료: {config.ip}:{config.port}"}
    else:
        return {"status": "error", "message": "❌ LiDAR 설정 실패"}


@app.get("/check_connection")
def check_connection(ip: str, port: int):
    """
    입력된 IP/Port가 실제 LiDAR 데이터가 수신된 IP/Port와 일치하는지 확인
    """
    # 최신 LiDAR 데이터 확인
    lidar_data = listener.get_latest_data().get("lidar_data", [])

    if not lidar_data:
        return {"status": "error", "message": "❌ LiDAR 데이터 없음"}

    # 실제 데이터가 수신된 IP 주소 가져오기
    actual_ip, actual_port = listener.get_last_received_source()
    print(f"✅ Actual IP/Port: {actual_ip}:{actual_port}")

    # 입력된 IP/포트와 실제 수신 IP/포트 비교
    if ip == actual_ip and int(port) == actual_port:
        return {"status": "success", "message": f"✅ LiDAR 연결 성공: {ip}:{port}"}
    else:
        return {"status": "error", "message": f"❌ IP 또는 포트를 확인해주세요"}


def run_server():
    uvicorn.run(app, host="0.0.0.0", port=8000)


if __name__ == "__main__":
    listener.start_listener()
    server_thread = threading.Thread(target=run_server, daemon=True)
    server_thread.start()
    input("Press Enter to stop the server...\n")
