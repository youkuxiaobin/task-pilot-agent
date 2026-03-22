import importlib

import yaml

from config.config import agentSettings


def get_prompt(prompt_file: str):
	base_path = importlib.resources.files("tools.mcp_local.prompt")
	lang = getattr(agentSettings, "lang", "ch").lower()
	candidates = []
	if lang and lang != "ch":
		candidates.append(f"{prompt_file}_{lang}.yaml")
	candidates.append(f"{prompt_file}.yaml")

	for filename in candidates:
		try:
			text = base_path.joinpath(filename).read_text()
		except FileNotFoundError:
			continue
		data = yaml.safe_load(text)
		if data is not None:
			return data
	raise FileNotFoundError(f"Prompt file not found for {prompt_file}; tried {candidates}")
