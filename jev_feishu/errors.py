class CopilotError(Exception):
    """Safe-to-display error; never place provider bodies or credentials here."""

class ConfigError(CopilotError):
    pass

class PermissionDenied(CopilotError):
    pass

class ProviderError(CopilotError):
    pass

class ContractError(CopilotError):
    pass
