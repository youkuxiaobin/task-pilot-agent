from urllib.parse import urlparse
import requests
import tempfile
import pathlib
import mimetypes
import base64
import ipaddress
import socket

class FileUtils:
    @staticmethod
    def is_web_url(url: str) -> bool:
        parsed_url = urlparse(url)
        return all([parsed_url.scheme, parsed_url.netloc])
    
    @staticmethod
    def get_file_ext(file_path: str) -> str:
        if FileUtils.is_web_url(file_path):
            return pathlib.Path(urlparse(file_path).path).suffix
        return pathlib.Path(file_path).suffix

    @staticmethod
    def download_file(url: str, save_path: str = None) -> str:
        """Download file from web. Return the saved path"""
        # if not save_path, use tempfile
        if not save_path:
            save_path = tempfile.NamedTemporaryFile(
                suffix=FileUtils.get_file_ext(url),
                delete=False,
            ).name
        response = requests.get(url)
        response.raise_for_status()
        with open(save_path, "wb") as f:
            f.write(response.content)
        return save_path
    
    @staticmethod
    def get_mime_type(file_path: str) -> str:
        mime_type, _ = mimetypes.guess_type(file_path)
        return mime_type or "application/octet-stream"
    
    @staticmethod
    def encode_to_base64(file_path: str) -> str:
        with open(file_path, 'rb') as file:
            data = file.read()
            encoded = base64.b64encode(data).decode('utf-8')
            mime_type = FileUtils.get_mime_type(file_path)
            return f"data:{mime_type};base64,{encoded}"

    @staticmethod
    def is_internal_url(url: str) -> bool:
        """
        判断一个 URL 是否是内网地址
        
        Args:
            url: 要检查的 URL
            
        Returns:
            bool: True 表示是内网地址，False 表示是外网地址
        """
        try:
            # 解析 URL 获取主机名
            parsed = urlparse(url)
            hostname = parsed.hostname
            
            if not hostname:
                return False
            
            # 检查是否是 localhost
            if hostname.lower() in ['localhost', '127.0.0.1', '::1']:
                return True
            
            # 尝试将主机名解析为 IP 地址
            try:
                # 如果主机名本身就是 IP 地址
                ip = ipaddress.ip_address(hostname)
            except ValueError:
                # 如果是域名，尝试 DNS 解析
                try:
                    ip_str = socket.gethostbyname(hostname)
                    ip = ipaddress.ip_address(ip_str)
                except (socket.gaierror, socket.herror):
                    # 无法解析的域名，可能是本地域名
                    # 检查是否是 .local 或其他本地域名
                    if hostname.endswith('.local'):
                        return True
                    return False
            
            # 检查是否是私有地址
            return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
            
        except Exception:
            return False