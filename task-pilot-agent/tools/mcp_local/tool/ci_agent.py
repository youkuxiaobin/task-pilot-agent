import json
import re
from collections.abc import Generator
from typing import Any, Optional
import uuid
from smolagents import (
    CodeAgent,
    AgentGenerationError,
    LogLevel,
    AgentParsingError,
    fix_final_answer_code,
    parse_code_blobs,
    AgentExecutionError,
    ToolCall,
    truncate_content,
    YELLOW_HEX,
    ActionOutput,
    Model,
    Tool,
    PromptTemplates,
	ActionStep,
	ChatMessageStreamDelta,
	agglomerate_stream_deltas,
	ToolOutput,
	CODEAGENT_RESPONSE_FORMAT,
)
from smolagents.utils import extract_code_from_text
from rich.text import Text
from rich.console import Group
from rich.live import Live
from rich.markdown import Markdown

from tools.mcp_local.model.code import CodeOutput
from tools.mcp_local.tool.final_answer_check import FinalAnswerCheck
from tools.mcp_local.util.file_util import generate_data_id
from tools.mcp_local.util.log_util import timer


class CIAgent(CodeAgent):
    def __init__(
        self,
        tools: list[Tool],
        model: Model,
        prompt_templates: PromptTemplates | None = None,
        additional_authorized_imports: list[str] | None = None,
        planning_interval: int | None = None,
        executor_type: str | None = "local",
        executor_kwargs: dict[str, Any] | None = None,
		grammar: dict[str, str] | None = None,
        output_dir: Optional[str] = None,
        *args,
        **kwargs,
    ):
        self.output_dir = output_dir
        self.grammar = grammar
        super().__init__(
			tools=tools,
			model=model,
			prompt_templates=prompt_templates,
			planning_interval=planning_interval,
			additional_authorized_imports=additional_authorized_imports,
			executor_type=executor_type,
			executor_kwargs=executor_kwargs,
            **kwargs,
        )

    @timer()
    def _step_stream(
        self, memory_step: ActionStep
    ) -> Generator[ChatMessageStreamDelta | ToolCall | ToolOutput | ActionOutput | CodeOutput]:
        """
        Run a single ReAct step with detailed logging aligned with the latest smolagents behaviour.
        """
        memory_messages = self.write_memory_to_messages()
        input_messages = memory_messages.copy()
        self.input_messages = input_messages
        memory_step.model_input_messages = input_messages

        stop_sequences = ["Observation:", "Calling tools:"]
        if self.code_block_tags[1] not in self.code_block_tags[0]:
            stop_sequences.append(self.code_block_tags[1])

        model_request_id = str(uuid.uuid4())

        try:
            additional_args: dict[str, Any] = {"extra_headers": {"x-ms-client-request-id": model_request_id}}
            if self._use_structured_outputs_internally:
                additional_args["response_format"] = CODEAGENT_RESPONSE_FORMAT

            if self.stream_outputs:
                output_stream = self.model.generate_stream(
                    input_messages,
                    stop_sequences=stop_sequences,
                    **additional_args,
                )
                chat_message_stream_deltas: list[ChatMessageStreamDelta] = []
                with Live("", console=self.logger.console, vertical_overflow="visible") as live:
                    for event in output_stream:
                        chat_message_stream_deltas.append(event)
                        live.update(
                            Markdown(agglomerate_stream_deltas(chat_message_stream_deltas).render_as_markdown())
                        )
                        yield event
                chat_message = agglomerate_stream_deltas(chat_message_stream_deltas)
                self.logger.log_markdown(
                    content=chat_message.content or "",
                    title="Output message of the LLM:",
                    level=LogLevel.DEBUG,
                )
            else:
                chat_message = self.model.generate(
                    input_messages,
                    stop_sequences=stop_sequences,
                    **additional_args,
                )
                self.logger.log_markdown(
                    content=chat_message.content or "",
                    title="Output message of the LLM:",
                    level=LogLevel.DEBUG,
                )

            output_text = chat_message.content or ""
            memory_step.model_output_message = chat_message
            if (
                not self._use_structured_outputs_internally
                and output_text
                and self.code_block_tags[0] in output_text
                and not output_text.strip().endswith(self.code_block_tags[1])
            ):
                output_text += self.code_block_tags[1]
                memory_step.model_output_message.content = output_text

            memory_step.model_output = output_text
            memory_step.token_usage = getattr(chat_message, "token_usage", None)

        except Exception as e:
            raise AgentGenerationError(f"Error in generating model output:\n{e}", self.logger) from e

        self.logger.log(
            f"[{YELLOW_HEX}]ReAct[{len(self.memory.steps) + 1}] model output ready",
            level=LogLevel.DEBUG,
        )

        try:
            if self._use_structured_outputs_internally:
                structured = json.loads(output_text)["code"]
                code_action = extract_code_from_text(structured, self.code_block_tags) or structured
            else:
                code_action = parse_code_blobs(output_text, self.code_block_tags)
            code_action = fix_final_answer_code(code_action)
            for tag in filter(None, [self.code_block_tags[0], self.code_block_tags[1], "<code>", "</code>"]):
                code_action = code_action.replace(tag, "")
            code_action = re.sub(r"</code>?$", "", code_action.strip(), flags=re.IGNORECASE)
            memory_step.code_action = code_action
        except Exception as e:
            error_msg = f"Error in code parsing:\n{e}\nMake sure to provide correct code blobs."
            raise AgentParsingError(error_msg, self.logger)

        tool_call = ToolCall(
            name="python_interpreter",
            arguments=code_action,
            id=f"call_{len(self.memory.steps)}",
        )
        memory_step.tool_calls = [tool_call]
        yield tool_call

        self.logger.log_code(title="Executing parsed code:", content=code_action, level=LogLevel.INFO)

        execution_logs = ""
        execution_outputs_console: list[Text] = []
        try:
            code_output = self.python_executor(code_action)
            execution_logs = getattr(code_output, "logs", "") or ""
            if execution_logs:
                execution_outputs_console += [
                    Text("Execution logs:", style="bold"),
                    Text(execution_logs),
                ]
            observation = "Execution logs:\n" + execution_logs
        except Exception as e:
            if hasattr(self.python_executor, "state") and "_print_outputs" in self.python_executor.state:
                execution_logs = str(self.python_executor.state["_print_outputs"])
                if execution_logs:
                    execution_outputs_console += [
                        Text("Execution logs:", style="bold"),
                        Text(execution_logs),
                    ]
                    memory_step.observations = "Execution logs:\n" + execution_logs
                    self.logger.log(Group(*execution_outputs_console), level=LogLevel.INFO)
            error_msg = str(e)
            if "Import of " in error_msg and " is not allowed" in error_msg:
                self.logger.log(
                    "[bold red]Warning: unauthorized import encountered during execution.",
                    level=LogLevel.INFO,
                )
            raise AgentExecutionError(error_msg, self.logger)

        truncated_output = truncate_content(str(getattr(code_output, "output", "")))
        observation += "Last output from code snippet:\n" + truncated_output
        memory_step.observations = observation

        if not getattr(code_output, "is_final_answer", False):
            execution_outputs_console += [Text(f"Out: {truncated_output}")]
        if execution_outputs_console:
            self.logger.log(Group(*execution_outputs_console), level=LogLevel.INFO)

        if matcher := re.search(r"Task:\s?(.*)", output_text):
            file_name = f"{matcher.group(1).replace(' ', '')}.py"
        else:
            file_name = f"{generate_data_id('index')}.py"
        yield CodeOutput(code=code_action, file_name=file_name)

        final_checker = FinalAnswerCheck(
            input_messages=self.input_messages,
            execution_logs=execution_logs,
            model=self.model,
            task=self.task,
            prompt_temps=self.prompt_templates,
            memory_step=memory_step,
            grammar=self.grammar,
            request_id=f"{model_request_id}-final",
        )
        final_flag, final_output = final_checker.check_is_final_answer()
        final_payload = final_output if final_output is not None else truncated_output
        memory_step.action_output = final_payload

        self.logger.log(
            f"[{YELLOW_HEX}]ReAct[{len(self.memory.steps) + 1}] final={'yes' if final_flag else 'no'}",
            level=LogLevel.INFO,
        )

        yield ActionOutput(output=final_payload, is_final_answer=final_flag)
