# src/common/errors.py

class DomainError(Exception):
    """Base class for domain errors."""
    pass

class InsufficientCashError(DomainError):
    """Raised when an account has insufficient cash for a transaction."""
    pass

class InsufficientPositionError(DomainError):
    """Raised when an account has insufficient position for a transaction."""
    pass

class OrderNotFoundError(DomainError):
    """Raised when an order is not found."""
    pass

class InvalidOrderStateError(DomainError):
    """Raised when an operation is attempted on an order in an invalid state."""
    pass
