# -*- coding: utf-8 -*-
from __future__ import annotations
import uuid
from contextlib import contextmanager
from typing import Any, Dict, List, Optional
from mcp.server.fastmcp import FastMCP, Context
from mcp.server.session import ServerSession

from tools.mcp_local.model.code import ActionOutput, CodeOutput
from tools.mcp_local.tool.code_interpreter import code_interpreter_agent
from tools.mcp_local.tool.report import report as report_agent
from tools.mcp_local.tool.deepsearch import DeepSearch
from tools.mcp_local.tool.browser_agent import BrowserAgent
from tools.mcp_local.tool.audio_toolkit import AudioToolkit
from tools.mcp_local.tool.image_toolkit import ImageToolkit
from tools.mcp_local.tool.video_toolkit import VideoToolkit
from tools.mcp_local.tool.filesystem import (
    copy_file as filesystem_copy_file,
    create_directory as filesystem_create_directory,
    delete_path as filesystem_delete_path,
    edit_file as filesystem_edit_file,
    file_stat as filesystem_file_stat,
    glob_paths as filesystem_glob_paths,
    grep_files as filesystem_grep_files,
    list_directory as filesystem_list_directory,
    move_file as filesystem_move_file,
    read_file as filesystem_read_file,
    shell_exec as filesystem_shell_exec,
    write_file as filesystem_write_file,
)
from tools.mcp_local.tool.image_generation import text_to_image as text_to_image_run
from tools.mcp_local.tool.management_tools import (
    config_read as config_read_run,
    config_update as config_update_run,
    create_subagent as create_subagent_run,
    install_skill as install_skill_run,
    load_skill as load_skill_run,
    mcp_manager_add_server as mcp_manager_add_server_run,
    mcp_manager_list_servers as mcp_manager_list_servers_run,
    mcp_manager_write_manifest as mcp_manager_write_manifest_run,
    memory_add as memory_add_run,
    memory_delete as memory_delete_run,
    memory_search as memory_search_run,
    search_skills as search_skills_run,
    send_message as send_message_run,
    set_skill_enabled as set_skill_enabled_run,
)
from tools.mcp_local.tool.process_manager import (
    list_process_commands as list_process_commands_run,
    poll_process_command as poll_process_command_run,
    start_process_command as start_process_command_run,
    stop_process_command as stop_process_command_run,
    write_process_command as write_process_command_run,
)
from tools.mcp_local.util.file_util import upload_file as upload_file_util, _FILE_SERVER_BASE
from tools.mcp_local.model.context import RequestIdCtx
from utils.logger import configure_log_context, clear_log_context

from tools.mcp_local.tool.weather import get_current_weather_run, get_weather_forecast_run
from tools.mcp_local.tool.simple_search import WebReader, WebSearch



def register_all_tools(mcp: FastMCP) -> None:
    @contextmanager
    def request_trace_context(request_id: Optional[str]):
        if not request_id:
            yield
            return
        previous_request_id = RequestIdCtx.request_id
        configure_log_context(trace_id=request_id)
        RequestIdCtx.request_id = request_id
        try:
            yield
        finally:
            RequestIdCtx.request_id = previous_request_id
            clear_log_context()

    def _stringify_input_content(input_content: Any) -> Optional[str]:
        """Make sure input_content is a string (or None) before passing to the agent."""
        if input_content is None:
            return None
        try:
            return input_content if isinstance(input_content, str) else str(input_content)
        except Exception:
            # repr is safer than str for objects with failing __str__
            return repr(input_content)


    async def _accumulate_code_interpreter_stream(task: str, file_names: Optional[List[str]], request_id: str, input_content: Optional[str]) -> Dict[str, Any]:
    	text_buffer = ""
    	final_action: Optional[ActionOutput] = None
    	code_blocks: List[CodeOutput] = []

    	async for chunk in code_interpreter_agent(task=task, file_names=file_names or [], input_data=input_content, request_id=request_id, stream=True):
    		if isinstance(chunk, CodeOutput):
    			code_blocks.append(chunk)
    		elif isinstance(chunk, ActionOutput):
    			final_action = chunk
    		else:
    			text_buffer += str(chunk)

    	upload_target_name = f"{uuid.uuid4().hex}_code_output.txt"
    	upload_target_type = "txt"

    	file_info = [
    		await upload_file_util(
    			content=final_action.content if final_action else text_buffer,
    			file_name=upload_target_name,
    			request_id=request_id,
    			file_type=upload_target_type,
    		)
    	]

    	return {
    		"code": 200,
    		"data": final_action.content if final_action else text_buffer,
    		"fileInfo": file_info,
    		"requestId": request_id,
    		"codeBlocks": [{"code": b.code, "fileInfo": b.file_list} for b in code_blocks],
    	}

    @mcp.tool(
        name="code_interpreter",
        description=(
            "Code interpreter 工具，执行 Python 任务并回传结果。\n"
            "\n"
            "功能：\n"
            "- 自动读取 file_names 指定的文件（支持 .csv/.xlsx/.txt/.md/.html/.docx/.xml/.pdf/.png/.jpg/.jpeg/.gif）。\n"
            "- 执行生成的 Python 代码并上传结果附件，包含代码块与最终回答。\n"
            "- 根据接口编写Python 代码，解析，完成任务。\n"
            "\n"
            "参数说明：\n"
            "- task (str): 必填，说明需要完成的分析或处理任务。\n"
            "- request_id (str): 必填，用于标记请求并匹配上传的输出文件。\n"
            "- input_content (str, 可选): 任务使用输入数据。"
            "- file_names (List[str], 可选): 任务使用的文件名列表，应为已上传文件的名称。"
        ),
    )
    async def code_interpreter(
    	task: str,
    	request_id: str,
    	input_content: Optional[Any] = None,
    	file_names: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
    	"""Run the code interpreter tool, aggregate streaming output, and upload generated artifacts.

    	Args:
    	    task: Natural language instruction describing the Python work.
    	    request_id: Request identifier reused when uploading results.
    	    file_names: Optional list of uploaded filenames required by the task.
    	"""
    	with request_trace_context(request_id):
    		normalized_input = _stringify_input_content(input_content)
    		if file_names:
    			for idx, f_name in enumerate(file_names):
    				if not f_name.startswith("/") and not f_name.startswith("http"):
    					file_names[idx] = f"{_FILE_SERVER_BASE}/download_file/{request_id}/{f_name}"

    		return await _accumulate_code_interpreter_stream(
    			task=task,
    			file_names=file_names,
    			request_id=request_id,
    			input_content=normalized_input,
    		)

    @mcp.tool(
        name="browser_agent",
        description=(
            "浏览器智能体，可以在浏览器沙箱中打开网页、点击、填写表单并返回任务结果。"
            "适合需要真实页面交互的浏览器任务。"
        ),
    )
    async def browser_agent(task: str) -> Dict[str, Any]:
        ret = await BrowserAgent().run(task)
        return ret.model_dump()

    @mcp.tool(name="audio_tool", description="音频工具，可以分析音频文件")
    async def audio_tool(path: str, query: str) -> str:
        return await AudioToolkit().audio_qa(path, query)

    @mcp.tool(name="image_tool", description="图片工具，使用模型来理解图片内容")
    async def image_tool(path: str, query: str) -> str:
        return await ImageToolkit().image_qa(path, query)

    @mcp.tool(name="video_tool", description="视频工具，可以分析视频文件")
    async def video_tool(path: str, query: str) -> str:
        return await VideoToolkit().video_qa(path, query)


    @mcp.tool(
        name="file_read",
        description=(
            "读取 Linux/macOS/Windows 本机文件内容。支持绝对路径和相对于任务工作目录的相对路径。"
            "默认最多读取 128KB，可通过 max_bytes 和 offset 分段读取大文件。"
        ),
    )
    async def file_read(
        path: str,
        encoding: str = "utf-8",
        max_bytes: int = 131072,
        offset: int = 0,
        as_base64: bool = False,
        work_dir: Optional[str] = None,
    ) -> Dict[str, Any]:
        return await filesystem_read_file(
            path,
            encoding=encoding,
            max_bytes=max_bytes,
            offset=offset,
            as_base64=as_base64,
            work_dir=work_dir,
        )


    @mcp.tool(
        name="file_write",
        description=(
            "写入文本文件。为避免误改系统文件，目标路径必须位于任务工作目录内；"
            "支持 overwrite 和 append。"
        ),
    )
    async def file_write(
        path: str,
        content: str,
        mode: str = "overwrite",
        encoding: str = "utf-8",
        create_dirs: bool = True,
        work_dir: Optional[str] = None,
    ) -> Dict[str, Any]:
        return await filesystem_write_file(
            path,
            content,
            mode=mode,
            encoding=encoding,
            create_dirs=create_dirs,
            work_dir=work_dir,
        )


    @mcp.tool(
        name="file_edit",
        description="基于精确字符串匹配做文件局部替换。目标文件必须位于任务工作目录内。",
    )
    async def file_edit(
        path: str,
        old_text: str,
        new_text: str,
        expected_replacements: int = 1,
        encoding: str = "utf-8",
        work_dir: Optional[str] = None,
    ) -> Dict[str, Any]:
        return await filesystem_edit_file(
            path,
            old_text,
            new_text,
            expected_replacements=expected_replacements,
            encoding=encoding,
            work_dir=work_dir,
        )


    @mcp.tool(
        name="file_list",
        description="列出目录内容。支持 Linux/macOS/Windows 路径，支持递归和最大返回数量限制。",
    )
    async def file_list(
        path: str = ".",
        recursive: bool = False,
        max_entries: int = 200,
        work_dir: Optional[str] = None,
    ) -> Dict[str, Any]:
        return await filesystem_list_directory(
            path,
            recursive=recursive,
            max_entries=max_entries,
            work_dir=work_dir,
        )


    @mcp.tool(name="file_glob", description="按通配符在目录下搜索文件或目录。")
    async def file_glob(
        pattern: str,
        root: str = ".",
        include_hidden: bool = False,
        max_results: int = 200,
        work_dir: Optional[str] = None,
    ) -> Dict[str, Any]:
        return await filesystem_glob_paths(
            pattern,
            root=root,
            include_hidden=include_hidden,
            max_results=max_results,
            work_dir=work_dir,
        )


    @mcp.tool(name="file_grep", description="在文件内容中搜索文本或正则表达式。")
    async def file_grep(
        query: str,
        root: str = ".",
        file_pattern: str = "*",
        regex: bool = False,
        case_sensitive: bool = False,
        max_results: int = 200,
        context_lines: int = 0,
        work_dir: Optional[str] = None,
    ) -> Dict[str, Any]:
        return await filesystem_grep_files(
            query,
            root=root,
            file_pattern=file_pattern,
            regex=regex,
            case_sensitive=case_sensitive,
            max_results=max_results,
            context_lines=context_lines,
            work_dir=work_dir,
        )


    @mcp.tool(name="file_stat", description="查看文件或目录的基础信息，包括类型、大小、时间和权限。")
    async def file_stat(path: str, work_dir: Optional[str] = None) -> Dict[str, Any]:
        return await filesystem_file_stat(path, work_dir=work_dir)


    @mcp.tool(name="directory_create", description="在任务工作目录内创建目录，支持自动创建父目录。")
    async def directory_create(
        path: str,
        exist_ok: bool = True,
        work_dir: Optional[str] = None,
    ) -> Dict[str, Any]:
        return await filesystem_create_directory(path, exist_ok=exist_ok, work_dir=work_dir)


    @mcp.tool(
        name="file_copy",
        description="复制文件。源文件可为本机可读路径，目标路径必须位于任务工作目录内。",
    )
    async def file_copy(
        source: str,
        destination: str,
        overwrite: bool = False,
        work_dir: Optional[str] = None,
    ) -> Dict[str, Any]:
        return await filesystem_copy_file(source, destination, overwrite=overwrite, work_dir=work_dir)


    @mcp.tool(
        name="file_move",
        description="移动或重命名任务工作目录内的文件或目录，目标也必须在任务工作目录内。",
    )
    async def file_move(
        source: str,
        destination: str,
        overwrite: bool = False,
        work_dir: Optional[str] = None,
    ) -> Dict[str, Any]:
        return await filesystem_move_file(source, destination, overwrite=overwrite, work_dir=work_dir)


    @mcp.tool(
        name="file_delete",
        description="删除任务工作目录内的文件或目录。目录删除需要 recursive=true。",
    )
    async def file_delete(
        path: str,
        recursive: bool = False,
        work_dir: Optional[str] = None,
    ) -> Dict[str, Any]:
        return await filesystem_delete_path(path, recursive=recursive, work_dir=work_dir)


    @mcp.tool(
        name="shell_exec",
        description=(
            "在 Linux/macOS/Windows 上执行 Shell 命令。高风险工具，只应在明确授权时启用；"
            "工作目录会限制在任务工作目录内。"
        ),
    )
    async def shell_exec(
        command: str,
        working_dir: Optional[str] = None,
        timeout: int = 30,
        max_output_chars: int = 12000,
        work_dir: Optional[str] = None,
    ) -> Dict[str, Any]:
        return await filesystem_shell_exec(
            command,
            working_dir=working_dir,
            timeout=timeout,
            max_output_chars=max_output_chars,
            work_dir=work_dir,
        )


    @mcp.tool(name="process_command_start", description="启动长生命周期命令行进程。高风险工具，默认不应启用。")
    async def process_command_start(
        command: str,
        working_dir: str = ".",
        work_dir: Optional[str] = None,
    ) -> Dict[str, Any]:
        return await start_process_command_run(command, working_dir=working_dir, work_dir=work_dir)


    @mcp.tool(name="process_command_poll", description="查看长生命周期命令行进程状态和最近输出。")
    async def process_command_poll(process_id: str, max_output_chars: int = 12000) -> Dict[str, Any]:
        return await poll_process_command_run(process_id, max_output_chars=max_output_chars)


    @mcp.tool(name="process_command_write", description="向长生命周期命令行进程写入标准输入。高风险工具，默认不应启用。")
    async def process_command_write(process_id: str, input_text: str) -> Dict[str, Any]:
        return await write_process_command_run(process_id, input_text)


    @mcp.tool(name="process_command_stop", description="停止长生命周期命令行进程。")
    async def process_command_stop(process_id: str, kill: bool = False) -> Dict[str, Any]:
        return await stop_process_command_run(process_id, kill=kill)


    @mcp.tool(name="process_command_list", description="列出当前仍由工具管理的长生命周期进程。")
    async def process_command_list() -> Dict[str, Any]:
        return await list_process_commands_run()


    @mcp.tool(name="text_to_image", description="根据文本描述生成图片，并把图片保存到任务工作目录。")
    async def text_to_image(
        prompt: str,
        size: str = "1024x1024",
        output_path: Optional[str] = None,
        model: Optional[str] = None,
        work_dir: Optional[str] = None,
    ) -> Dict[str, Any]:
        return await text_to_image_run(
            prompt,
            size=size,
            output_path=output_path,
            model=model,
            work_dir=work_dir,
        )


    @mcp.tool(name="config_read", description="读取当前运行配置，默认会隐藏敏感值。")
    async def config_read(section: Optional[str] = None, include_sensitive: bool = False) -> Dict[str, Any]:
        return await config_read_run(section=section, include_sensitive=include_sensitive)


    @mcp.tool(name="config_update", description="修改应用或 Agent 配置。高风险工具，默认需要显式放开。")
    async def config_update(
        target: str,
        field_path: str,
        value: Any,
        agent_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        return await config_update_run(target, field_path, value, agent_id=agent_id)


    @mcp.tool(name="mcp_manager_list_servers", description="列出当前配置的 MCP Server。")
    async def mcp_manager_list_servers() -> Dict[str, Any]:
        return await mcp_manager_list_servers_run()


    @mcp.tool(name="mcp_manager_write_manifest", description="把当前 MCP 配置导出为 mcp.json 到任务工作目录。")
    async def mcp_manager_write_manifest(
        output_path: str = "mcp.json",
        work_dir: Optional[str] = None,
    ) -> Dict[str, Any]:
        return await mcp_manager_write_manifest_run(output_path=output_path, work_dir=work_dir)


    @mcp.tool(name="mcp_manager_add_server", description="向应用配置追加 MCP Server。高风险工具，默认需要显式放开。")
    async def mcp_manager_add_server(
        url: str,
        tool_prefix: str,
        transport: str = "streamable-http",
        authorization: str = "",
    ) -> Dict[str, Any]:
        return await mcp_manager_add_server_run(
            url,
            tool_prefix,
            transport=transport,
            authorization=authorization,
        )


    @mcp.tool(name="message_send", description="向本地任务流或配置的外部 webhook 发送消息。")
    async def message_send(
        channel: str,
        message: str,
        ctx: Context[ServerSession, None],
        title: str = "",
        webhook_url: Optional[str] = None,
    ) -> Dict[str, Any]:
        result = await send_message_run(channel, message, title=title, webhook_url=webhook_url)
        if channel == "local_task" and ctx is not None:
            await ctx.info(message)
        return result


    @mcp.tool(name="skill_search", description="搜索当前可用 Agent 能力和任务工作目录内安装的技能。")
    async def skill_search(
        query: str = "",
        limit: int = 20,
        work_dir: Optional[str] = None,
        include_disabled: bool = False,
        agent_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        return await search_skills_run(
            query=query,
            limit=limit,
            work_dir=work_dir,
            include_disabled=include_disabled,
            agent_id=agent_id,
        )


    @mcp.tool(name="skill_load", description="加载一个已存在的 Agent 能力说明或任务本地技能说明。")
    async def skill_load(skill_id: str, work_dir: Optional[str] = None, include_disabled: bool = False) -> Dict[str, Any]:
        return await load_skill_run(skill_id, work_dir=work_dir, include_disabled=include_disabled)


    @mcp.tool(name="skill_install", description="把技能说明安装到任务工作目录，供本次任务后续使用。")
    async def skill_install(
        skill_id: str,
        content: str,
        work_dir: Optional[str] = None,
        enabled: bool = True,
        agent_ids: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        return await install_skill_run(skill_id, content, work_dir=work_dir, enabled=enabled, agent_ids=agent_ids)


    @mcp.tool(name="skill_set_enabled", description="启用或禁用任务工作目录内安装的技能。")
    async def skill_set_enabled(skill_id: str, enabled: bool, work_dir: Optional[str] = None) -> Dict[str, Any]:
        return await set_skill_enabled_run(skill_id, enabled, work_dir=work_dir)


    @mcp.tool(name="create_subagent", description="创建子 Agent 配置。默认只写入任务工作目录，不注册为运行时 Agent。")
    async def create_subagent(
        agent_id: str,
        name: str,
        description: str,
        system_prompt: str,
        tools: Optional[List[str]] = None,
        enable_in_registry: bool = False,
        work_dir: Optional[str] = None,
    ) -> Dict[str, Any]:
        return await create_subagent_run(
            agent_id,
            name,
            description,
            system_prompt,
            tools=tools,
            enable_in_registry=enable_in_registry,
            work_dir=work_dir,
        )


    @mcp.tool(name="memory_search", description="按当前用户、Agent 或 run 范围搜索长期记忆。")
    async def memory_search(
        query: str,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        run_id: Optional[str] = None,
        limit: int = 10,
    ) -> Dict[str, Any]:
        return await memory_search_run(query, user_id=user_id, agent_id=agent_id, run_id=run_id, limit=limit)


    @mcp.tool(name="memory_add", description="写入长期记忆。")
    async def memory_add(
        content: str,
        user_id: str,
        agent_id: str,
        run_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        return await memory_add_run(content, user_id=user_id, agent_id=agent_id, run_id=run_id)


    @mcp.tool(name="memory_delete", description="删除长期记忆。")
    async def memory_delete(memory_id: str) -> Dict[str, Any]:
        return await memory_delete_run(memory_id)


    async def _accumulate_report_stream(
        task: str,
        request_id: str,
        file_type: str,
        stream_mode: Optional[Dict[str, Any]],
        stream: bool,
        ctx: Optional[Context[ServerSession, None]],
    ) -> Dict[str, Any]:
        """复用 HTTP 的报告生成逻辑，流式时通过日志推送分段内容。"""
        def _parser_html_content(content: str) -> str:
            if content.startswith("```\nhtml"):
                content = content[len("```\nhtml"):]
            if content.startswith("```html"):
                content = content[len("```html"):]
            if content.endswith("```"):
                content = content[:-3]
            return content

        async def _emit_stream_message(payload: Dict[str, Any]) -> None:
            if not stream or ctx is None:
                return
            await ctx.session.send_log_message(
                level="info",
                data=payload,
                logger="report_stream",
            )

        content = ""
        async for chunk in report_agent(
            task=task,
            request_id=request_id,
            file_type=file_type,
        ):
            chunk_text = str(chunk)
            if chunk_text:
                await _emit_stream_message(
                    {
                        "type": "report_chunk",
                        "chunk": chunk_text,
                        "requestId": request_id,
                        "fileType": file_type,
                        "streamMode": stream_mode or {"mode": "general"},
                    }
                )
            content += chunk_text

        if file_type in ["ppt", "html"]:
            content = _parser_html_content(content)


        file_info = []

        return {"code": 200, "data": content, "fileInfo": file_info, "requestId": request_id}



    @mcp.tool(
        name="report",
        description=(
            "生成报告类产物，支持 markdown、html、ppt。适合把搜索、文件或分析结果整理成可交付报告。"
        ),
    )
    async def report(
    	task: str,
    	request_id: str,
    	ctx: Context[ServerSession, None],
    	file_type: str = "markdown",
    	stream: bool = True,
    	stream_mode: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
    	with request_trace_context(request_id):
    		return await _accumulate_report_stream(
    			task=task,
    			request_id=request_id,
    			file_type=file_type,
    			stream_mode=stream_mode or {"mode": "general"},
    			stream=stream,
    			ctx=ctx,
    		)


    @mcp.tool(name="deepsearch", description="这是一个搜索工具，可以通过搜索内外网知识")
    async def deepsearch(
        query: str,
        request_id: str,
        ctx: Context[ServerSession, None]
    ) -> str:
        """深度搜索（聚合流式输出，返回完整文本）。"""
        with request_trace_context(request_id):
            max_loop = 1
            deepsearch = DeepSearch()
            acc = ""
        
            # 检查ctx是否可用
            if ctx is not None:
                await ctx.info(f"开始深度搜索: {query}")
        
            async for chunk in deepsearch.run(
                query=query,
                request_id=request_id,
                max_loop=max_loop,
                stream=False,
                stream_mode={"mode": "general"},
                ctx=ctx,
            ):
                acc += chunk
            
            if ctx is not None:
                await ctx.info("深度搜索完成")
        
            return acc


    @mcp.tool(
        name="web_search",
        description=(
            "轻量网页搜索工具：搜索网页，对返回页面做渲染/正文抓取，不做查询扩展、深度推理或最终答案生成。"
        ),
    )
    async def web_search(
        query: str,
        request_id: Optional[str] = None,
        max_results: int = 5,
        content_max_chars: int = 500,
        fetch_content: bool = True,
    ) -> Dict[str, Any]:
        with request_trace_context(request_id):
            return await WebSearch().run(
                query=query,
                request_id=request_id,
                max_results=max_results,
                content_max_chars=content_max_chars,
                fetch_content=fetch_content,
            )


    @mcp.tool(
        name="fetch_url",
        description=(
            "读取指定 URL 的页面正文：优先使用浏览器渲染读取，失败时退回普通 HTTP 页面解析。"
        ),
    )
    async def fetch_url(
        url: str,
        request_id: Optional[str] = None,
        content_max_chars: int = 4000,
    ) -> Dict[str, Any]:
        with request_trace_context(request_id):
            return await WebReader().run(
                url=url,
                request_id=request_id,
                content_max_chars=content_max_chars,
            )


    @mcp.tool(
        name="web_reader",
        description=(
            "读取已知网页 URL 的正文内容，等同于 fetch_url。适合搜索后继续打开来源页面。"
        ),
    )
    async def web_reader(
        url: str,
        request_id: Optional[str] = None,
        content_max_chars: int = 4000,
    ) -> Dict[str, Any]:
        with request_trace_context(request_id):
            return await WebReader().run(
                url=url,
                request_id=request_id,
                content_max_chars=content_max_chars,
            )


    @mcp.tool(name="get_current_weather", description="获取指定城市的实时天气")
    async def get_current_weather(city: str) ->  str:
        return await get_current_weather_run(city)


    @mcp.tool(name="get_weather_forecast", description="获取指定城市的天气预报(未来3-4天)")
    async def get_weather_forecast(city: str) -> str:
        return await get_weather_forecast_run(city)
