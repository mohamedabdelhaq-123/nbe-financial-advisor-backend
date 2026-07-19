"""HTML email bodies for this service's own outbound mail.

Mirrors the visual style of core/templates/emails/*.html on the Django side
(same brand color, card layout, footer copy) even though this is a separate
FastAPI service with no Django templating available — plain f-strings are
enough for the one email this service sends.
"""

_BASE = """\
<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>{title}</title>
  </head>
  <body
    style="margin:0;padding:0;background-color:#F3F5F4;
      font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;"
  >
    <table
      role="presentation"
      width="100%"
      cellpadding="0"
      cellspacing="0"
      style="background-color:#F3F5F4;padding:32px 16px;"
    >
      <tr>
        <td align="center">
          <table
            role="presentation"
            width="480"
            cellpadding="0"
            cellspacing="0"
            style="max-width:480px;width:100%;background-color:#FFFFFF;
              border-radius:12px;overflow:hidden;border:1px solid #E5E9E7;"
          >
            <tr>
              <td style="background-color:#2E6350;padding:24px 32px;">
                <span style="color:#FFFFFF;font-size:18px;font-weight:600;letter-spacing:-0.01em;">
                  Financial Advisor
                </span>
              </td>
            </tr>
            <tr>
              <td style="padding:32px;">{content}</td>
            </tr>
            <tr>
              <td style="padding:20px 32px;border-top:1px solid #E5E9E7;">
                <p style="margin:0;color:#8A938F;font-size:12px;line-height:1.5;">
                  National Bank of Egypt · If you didn't expect this email, you can
                  safely ignore it.
                </p>
              </td>
            </tr>
          </table>
        </td>
      </tr>
    </table>
  </body>
</html>
"""

_OTP_CONTENT = """\
  <h1 style="margin:0 0 12px;color:#111827;font-size:20px;font-weight:600;">
    Your verification code
  </h1>
  <p style="margin:0 0 24px;color:#4B5563;font-size:14px;line-height:1.6;">
    Enter this code to finish signing in to your bank account.
  </p>
  <table role="presentation" cellpadding="0" cellspacing="0">
    <tr>
      <td style="border-radius:8px;background-color:#F3F5F4;padding:16px 28px;">
        <span style="color:#111827;font-size:28px;font-weight:700;letter-spacing:0.3em;">
          {otp}
        </span>
      </td>
    </tr>
  </table>
  <p style="margin:24px 0 0;color:#9CA3AF;font-size:12px;line-height:1.6;">
    This code expires shortly. If you didn't request it, you can safely ignore
    this email.
  </p>
"""


def render_otp_email(otp: str) -> str:
    return _BASE.format(title="Your verification code", content=_OTP_CONTENT.format(otp=otp))
