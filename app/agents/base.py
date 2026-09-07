from abc import ABC, abstractmethod


class BaseAgent(ABC):
    @abstractmethod
    def run(self, input: dict) -> dict:
        """Implemented manually per agent. Do not fill in."""
        raise NotImplementedError
