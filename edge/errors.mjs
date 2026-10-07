/** A client-visible error whose message contains no transport secrets. */
export class ApiError extends Error {
  constructor(code, message, status = 400) {
    super(message);
    this.code = code;
    this.status = status;
  }
}
