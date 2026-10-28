const fs = require('fs');
module.exports = async (p) => {
  const pw = fs.readFileSync(require('os').homedir() + '/.local/share/PyPSA Studio/dev-admin-password', 'utf8').trim();
  await p.goto('http://127.0.0.1:5173/');
  await p.waitForTimeout(2000);
  if (await p.locator('input[type=password]').count()) {
    await p.locator('input[type=email], input[name=email]').first().fill('admin@example.com');
    await p.locator('input[type=password]').fill(pw);
    await p.locator('input[type=password]').press('Enter');
    await p.waitForTimeout(3000);
  }
  await p.getByRole('link', { name: /Campus base/ }).first().click();
  await p.waitForTimeout(4000);
};
