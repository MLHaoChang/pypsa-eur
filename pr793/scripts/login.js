const fs = require('fs');
module.exports = async (p) => {
  const pw = fs.readFileSync(process.env.HOME + '/.local/share/PyPSA Studio/dev-admin-password', 'utf8').trim();
  await p.goto('http://127.0.0.1:5173/');
  await p.waitForTimeout(1500);
  const inputs = p.locator('input');
  await inputs.nth(0).fill('admin@example.com'); await inputs.nth(1).fill(pw);
  await inputs.nth(1).press('Enter');
  await p.waitForTimeout(4000);
};
