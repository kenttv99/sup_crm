class SupportChatConfigError(RuntimeError):
    pass


class SupportTopicLifecycleError(RuntimeError):
    pass


class SupportTopicNotFoundError(SupportTopicLifecycleError):
    pass
