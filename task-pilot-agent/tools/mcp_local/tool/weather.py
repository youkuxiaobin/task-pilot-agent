from typing import Any, Dict, Optional
import httpx
import csv
import os
from mcp.server.fastmcp import FastMCP

from utils.logger import get_logger

mcp = FastMCP("amap-weather-mcp-server")

AMAP_API_BASE = "https://restapi.amap.com/v3/weather/weatherInfo"
AMAP_API_KEY = os.environ.get("AMAP_API_KEY", "a59bfb0a77f9c7e8244738a864b0eb03")
USER_AGENT = "amap-weather-mcp-server/1.0"

city_to_adcode = {}
logger = get_logger(__name__)

def load_city_adcode_map():
    """加载城市名称到adcode的映射"""
    try:
        current_dir = os.path.dirname(os.path.abspath(__file__))
        csv_file_path = os.path.join(current_dir, "AMap_adcode_citycode.csv")
        
        with open(csv_file_path, 'r', encoding='utf-8') as file:
            reader = csv.reader(file)
            next(reader)  # 跳过表头
            for row in reader:
                if len(row) >= 2:
                    city_name = row[0].strip()
                    adcode = row[1].strip()
                    city_to_adcode[city_name] = adcode
        return True
    except Exception as e:
        logger.error(f"加载城市编码文件失败: {e}")
        return False

# 初始加载城市编码数据
load_city_adcode_map()

def get_adcode_by_city(city_name: str) -> Optional[str]:
    """根据城市名称查找对应的adcode
    
    Args:
        city_name: 城市名称，如"北京市"、"上海市"等
        
    Returns:
        城市对应的adcode，如果未找到则返回None
    """
    # 先尝试直接匹配
    if city_name in city_to_adcode:
        return city_to_adcode[city_name]
    
    # 如果未找到，尝试添加"市"或"省"后缀再查找
    if not city_name.endswith("市") and not city_name.endswith("省"):
        city_with_suffix = city_name + "市"
        if city_with_suffix in city_to_adcode:
            return city_to_adcode[city_with_suffix]
            
        city_with_suffix = city_name + "省"
        if city_with_suffix in city_to_adcode:
            return city_to_adcode[city_with_suffix]
    
    # 对于区级城市，尝试判断是否为区名
    for full_name, code in city_to_adcode.items():
        if city_name in full_name and (full_name.endswith("区") or "区" in full_name):
            return code
    
    return None

async def make_amap_request(params: Dict[str, str]) -> Dict[str, Any]:
    """向高德地图API发送请求并获取天气数据
    
    Args:
        params: API请求参数
        
    Returns:
        API返回的JSON数据，如果请求失败则返回None
    """
    # 添加公共参数
    params["key"] = AMAP_API_KEY
    
    headers = {
        "User-Agent": USER_AGENT
    }
    
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(AMAP_API_BASE, params=params, headers=headers, timeout=30.0)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"API请求失败: {e}")
            return None

def format_current_weather(weather_data: Dict[str, Any]) -> str:
    """格式化实时天气信息
    
    Args:
        weather_data: 高德地图API返回的天气数据
        
    Returns:
        格式化后的天气信息字符串
    """
    if not weather_data or "lives" not in weather_data or not weather_data["lives"]:
        return "无法获取天气信息或数据格式错误"
    
    live = weather_data["lives"][0]
    content = f"""
城市: {live.get('city', '未知')}
天气: {live.get('weather', '未知')}
温度: {live.get('temperature', '未知')}°C
风向: {live.get('winddirection', '未知')}
风力: {live.get('windpower', '未知')}级
湿度: {live.get('humidity', '未知')}%
发布时间: {live.get('reporttime', '未知')}
"""
    return content

def format_forecast_weather(weather_data: Dict[str, Any]) -> str:
    """格式化天气预报信息
    
    Args:
        weather_data: 高德地图API返回的天气预报数据
        
    Returns:
        格式化后的天气预报信息字符串
    """
    if not weather_data or "forecasts" not in weather_data or not weather_data["forecasts"]:
        return "无法获取天气预报信息或数据格式错误"
    
    forecast = weather_data["forecasts"][0]
    city = forecast.get('city', '未知')
    casts = forecast.get('casts', [])
    
    if not casts:
        return f"{city}: 无天气预报数据"
    
    forecasts = []
    for cast in casts:
        day_forecast = f"""
日期: {cast.get('date', '未知')}
白天天气: {cast.get('dayweather', '未知')}
白天温度: {cast.get('daytemp', '未知')}°C
白天风向: {cast.get('daywind', '未知')}
白天风力: {cast.get('daypower', '未知')}级
夜间天气: {cast.get('nightweather', '未知')}
夜间温度: {cast.get('nighttemp', '未知')}°C
夜间风向: {cast.get('nightwind', '未知')}
夜间风力: {cast.get('nightpower', '未知')}级
"""
        forecasts.append(day_forecast)
    
    return f"城市: {city}\n\n" + "\n---\n".join(forecasts)
async def get_current_weather_run(city: str) ->  str:
    adcode = get_adcode_by_city(city)
    if not adcode:
        return f"无法找到城市'{city}'的编码，请检查城市名称是否正确"
    
    params = {
        "city": adcode,
        "extensions": "base"  # 获取实时天气
    }
    
    data = await make_amap_request(params)
    
    if not data:
        return f"获取{city}的天气信息失败"
    
    if data.get("status") != "1":
        return f"API返回错误: {data.get('info', '未知错误')}"
    
    return format_current_weather(data)
async def get_weather_forecast_run(city: str) -> str:
    adcode = get_adcode_by_city(city)
    if not adcode:
        return f"无法找到城市'{city}'的编码，请检查城市名称是否正确"
    
    params = {
        "city": adcode,
        "extensions": "all"  # 获取未来天气预报
    }
    
    data = await make_amap_request(params)
    
    if not data:
        return f"获取{city}的天气预报失败"
    
    if data.get("status") != "1":
        return f"API返回错误: {data.get('info', '未知错误')}"
    
    return format_forecast_weather(data)



if __name__ == "__main__":
    # 初始化并运行服务器
    mcp.run(transport='stdio')
