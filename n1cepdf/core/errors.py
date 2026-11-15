"""Exception hierarchy. Messages never contain document content or passwords."""


class N1cePdfError(Exception):
    pass


class InvalidPdfError(N1cePdfError):
    """File is not a PDF, is too large, or is malformed."""


class PasswordRequired(N1cePdfError):
    """Document is encrypted and needs a password to open."""


class WrongPassword(N1cePdfError):
    """A password was supplied but it is not valid."""


class EncryptedSaveRefused(N1cePdfError):
    """Saving an encrypted document unencrypted needs explicit authorization."""


class SaveValidationError(N1cePdfError):
    """Output file failed post-write validation; destination left untouched."""


class OperationCancelled(N1cePdfError):
    pass
