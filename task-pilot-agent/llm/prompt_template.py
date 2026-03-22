from typing import Dict


class PromptTemplate:
    """
    Lightweight named-variable template using Python's str.format syntax.

    Example:
      template = PromptTemplate("Hello {name}, today is {day}.")
      template.render({"name": "Alice", "day": "Monday"})
    """

    def __init__(self, template: str) -> None:
        self.template = template

    def render(self, variables: Dict[str, object]) -> str:
        try:
            return self.template.format(**variables)
        except KeyError as e:
            missing = str(e).strip("'")
            raise KeyError(f"Missing variable '{missing}' for template rendering")

