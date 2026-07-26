import PasswordTokenPage from './PasswordTokenPage'

export default function ResetPasswordPage() {
  return (
    <PasswordTokenPage
      mode="reset"
      subtitle="Use the reset link from your email to choose a new password and get back into your workspace."
      successMessage="Password reset. Sign in with your new password."
      title="Reset your password"
    />
  )
}
