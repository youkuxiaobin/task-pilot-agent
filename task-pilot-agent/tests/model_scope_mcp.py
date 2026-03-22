# pip install modelscope

from modelscope.hub.mcp_api import MCPApi

# Initialize the instance
api = MCPApi()
api.login("ms-2ff62bb9-0457-4fd2-ab82-2c56a262f00f")
servers = api.list_mcp_servers()
print(f"MCP Count: {servers['total_count']}")


operational_servers = api.list_operational_mcp_servers()

# Get MCP server information
for server in operational_servers['servers']:
    print(f"name: {server['name']}")
    
    # Get the URL
    for mcp_server in server['mcp_servers']:
        print(f"  {mcp_server['type']}: {mcp_server['url']}")