<?php
/**
 * /aicc2/index.php
 *
 * Server-side gate for the static AICC app.
 */

function aicc_page_session_start(): void {
    if (session_status() === PHP_SESSION_ACTIVE) return;
    $secure = (!empty($_SERVER['HTTPS']) && $_SERVER['HTTPS'] !== 'off');
    session_name('AICCAUTH');
    session_set_cookie_params(0, '/', '', $secure, true);
    session_start();
}

function aicc_page_authenticated(): bool {
    return !empty($_SESSION['aicc_auth_ok']) && !empty($_SESSION['aicc_auth_hp'])
        && !empty($_SESSION['aicc_auth_at']) && (time() - (int)$_SESSION['aicc_auth_at'] < 43200);
}

aicc_page_session_start();

if (aicc_page_authenticated()) {
    header('Content-Type: text/html; charset=utf-8');
    header('Cache-Control: no-store, must-revalidate');
    readfile(__DIR__ . '/index.html');
    exit;
}

header('Content-Type: text/html; charset=utf-8');
header('Cache-Control: no-store, must-revalidate');
?>
<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>랜스타 AICC 인증</title>
  <style>
    :root { --bg:#f5f5f7; --panel:#fff; --text:#1d1d1f; --muted:#6e6e73; --border:#d8d8df; --accent:#0071e3; --err:#d8382f; --ok:#0a8754; }
    * { box-sizing:border-box; }
    body { margin:0; min-height:100vh; display:grid; place-items:center; background:var(--bg); color:var(--text); font-family:-apple-system,BlinkMacSystemFont,"Pretendard","Noto Sans KR",sans-serif; }
    main { width:min(420px, calc(100vw - 32px)); background:var(--panel); border:1px solid var(--border); border-radius:14px; box-shadow:0 18px 50px rgba(20,24,40,.12); padding:28px; }
    h1 { margin:0 0 6px; font-size:22px; letter-spacing:0; }
    p { margin:0 0 20px; color:var(--muted); font-size:14px; line-height:1.6; }
    label { display:block; margin:14px 0 6px; font-size:13px; font-weight:700; }
    input { width:100%; height:44px; border:1px solid var(--border); border-radius:8px; padding:0 12px; font-size:15px; outline:none; }
    input:focus { border-color:var(--accent); box-shadow:0 0 0 3px rgba(0,113,227,.16); }
    button { width:100%; height:44px; margin-top:14px; border:0; border-radius:8px; color:#fff; background:var(--accent); font-size:14px; font-weight:700; cursor:pointer; }
    button:disabled { opacity:.55; cursor:not-allowed; }
    .secondary { background:#eef1f5; color:var(--text); border:1px solid var(--border); }
    .row { display:grid; grid-template-columns:1fr 120px; gap:8px; align-items:end; }
    .status { min-height:20px; margin-top:14px; font-size:13px; color:var(--muted); }
    .status.err { color:var(--err); }
    .status.ok { color:var(--ok); }
    .setup { display:none; margin-top:22px; padding-top:20px; border-top:1px solid var(--border); }
    .hint { font-size:12px; color:var(--muted); margin-top:10px; }
  </style>
</head>
<body>
  <main>
    <h1>랜스타 AICC</h1>
    <p>통합설정에 등록된 휴대폰 번호로 SMS 인증 후 접속할 수 있습니다.</p>

    <form id="sms-form">
      <label for="hp">휴대폰 번호</label>
      <div class="row">
        <input id="hp" inputmode="numeric" autocomplete="tel" placeholder="01012345678">
        <button type="button" id="send">인증 발송</button>
      </div>
      <label for="code">인증번호</label>
      <input id="code" inputmode="numeric" autocomplete="one-time-code" maxlength="6" placeholder="6자리">
      <button type="submit" id="verify">접속하기</button>
      <div class="status" id="status"></div>
    </form>

    <section class="setup" id="setup">
      <p>아직 허용 번호가 없습니다. 최초 1회만 통합설정 비밀번호로 접속 허용 번호를 등록합니다.</p>
      <label for="setup-hp">등록할 휴대폰 번호</label>
      <input id="setup-hp" inputmode="numeric" autocomplete="tel" placeholder="01012345678">
      <label for="setup-pw">통합설정 비밀번호</label>
      <input id="setup-pw" type="password" autocomplete="current-password" placeholder="통합설정 비밀번호">
      <button type="button" class="secondary" id="setup-save">허용 번호 등록</button>
      <div class="hint">등록 후 위 번호로 SMS 인증을 진행하세요.</div>
    </section>
  </main>
  <script>
    const $ = id => document.getElementById(id);
    const api = (action, body) => fetch('/aicc2/auth.php?action=' + action, {
      method: body ? 'POST' : 'GET',
      headers: body ? {'Content-Type':'application/json'} : {},
      body: body ? JSON.stringify(body) : undefined,
      cache: 'no-cache',
      credentials: 'same-origin'
    }).then(r => r.json().catch(() => ({ok:false,error:'응답을 읽지 못했습니다.'})));
    const setStatus = (msg, cls='') => { $('status').textContent = msg; $('status').className = 'status ' + cls; };
    const digits = v => (v || '').replace(/[^0-9]/g, '');

    api('status').then(j => {
      if (j.ok && j.authenticated) location.replace('/aicc2/');
      if (j.ok && !j.hasAllowedNumbers) $('setup').style.display = 'block';
    });

    $('send').addEventListener('click', async () => {
      const hp = digits($('hp').value);
      $('hp').value = hp;
      $('send').disabled = true;
      setStatus('인증번호 발송 중...');
      const j = await api('send', {hp});
      $('send').disabled = false;
      if (!j.ok) {
        setStatus(j.error || '발송 실패', 'err');
        if (j.needSetup) $('setup').style.display = 'block';
        return;
      }
      setStatus(j.message || '인증번호가 발송되었습니다.', 'ok');
      $('code').focus();
    });

    $('sms-form').addEventListener('submit', async e => {
      e.preventDefault();
      const hp = digits($('hp').value);
      const code = digits($('code').value);
      $('verify').disabled = true;
      setStatus('확인 중...');
      const j = await api('verify', {hp, code});
      $('verify').disabled = false;
      if (!j.ok) { setStatus(j.error || '인증 실패', 'err'); return; }
      setStatus('인증 완료. 이동합니다.', 'ok');
      location.replace('/aicc2/');
    });

    $('setup-save').addEventListener('click', async () => {
      const hp = digits($('setup-hp').value);
      const password = $('setup-pw').value;
      $('setup-save').disabled = true;
      setStatus('허용 번호 저장 중...');
      const j = await api('setup', {hp, password});
      $('setup-save').disabled = false;
      if (!j.ok) { setStatus(j.error || '저장 실패', 'err'); return; }
      $('hp').value = hp;
      $('setup').style.display = 'none';
      setStatus('허용 번호가 등록되었습니다. 인증번호를 발송하세요.', 'ok');
    });
  </script>
</body>
</html>
