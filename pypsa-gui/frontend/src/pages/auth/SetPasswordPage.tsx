import PasswordTokenPage from './PasswordTokenPage'

export default function SetPasswordPage() {
  return (
    <PasswordTokenPage
      mode="set"
      subtitle="Use the invitation link from your email to activate your account and choose a password."
      successMessage="Password set. You can sign in now."
      title="Set your password"
    />
  )
}
