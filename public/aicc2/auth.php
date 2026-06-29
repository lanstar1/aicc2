<?php
/**
 * /aicc2/auth.php
 *
 * AICC2 page access authentication by SMS.
 */

define('CS_ENTRY', 1);
require __DIR__ . '/../cs/lib/helpers.php';
require __DIR__ . '/../cs/lib/db.php';
require __DIR__ . '/../cs/lib/sms.php';

function aicc_auth_session_start(): void {
    if (session_status() === PHP_SESSION_ACTIVE) return;
    $secure = (!empty($_SERVER['HTTPS']) && $_SERVER['HTTPS'] !== 'off');
    session_name('AICCAUTH');
    session_set_cookie_params(0, '/', '', $secure, true);
    session_start();
}

function aicc_auth_cfg_file(): string {
    return dirname(__DIR__) . '/data/aicc_secure/config.json';
}

function aicc_auth_load_cfg(): array {
    $file = aicc_auth_cfg_file();
    if (!is_file($file)) return [];
    $cfg = json_decode((string)@file_get_contents($file), true);
    return is_array($cfg) ? $cfg : [];
}

function aicc_auth_save_cfg(array $cfg): bool {
    $dir = dirname(aicc_auth_cfg_file());
    if (!is_dir($dir)) {
        @mkdir($dir, 0700, true);
        @file_put_contents($dir . '/.htaccess', "Require all denied\nDeny from all\n");
        @file_put_contents($dir . '/index.html', '');
    }
    return @file_put_contents(aicc_auth_cfg_file(), json_encode($cfg, JSON_UNESCAPED_UNICODE | JSON_PRETTY_PRINT), LOCK_EX) !== false;
}

function aicc_auth_allowed_numbers(array $cfg): array {
    $raw = (string)($cfg['aicc_auth_phones'] ?? '');
    $parts = preg_split('/[\s,;|]+/', $raw);
    $out = [];
    foreach ($parts as $p) {
        $hp = cs_normalize_hp((string)$p);
        if ($hp !== '' && cs_is_valid_hp($hp)) $out[$hp] = true;
    }
    return array_keys($out);
}

function aicc_auth_mask_hp(string $hp): string {
    $hp = cs_normalize_hp($hp);
    if (strlen($hp) < 7) return $hp;
    return substr($hp, 0, 3) . '****' . substr($hp, -4);
}

function aicc_auth_log_file(): string {
    return dirname(aicc_auth_cfg_file()) . '/auth_log.jsonl';
}

function aicc_auth_log_event(string $event, string $hp = ''): void {
    $dir = dirname(aicc_auth_log_file());
    if (!is_dir($dir)) @mkdir($dir, 0700, true);
    $hp = cs_normalize_hp($hp);
    $row = [
        'at' => date('Y-m-d H:i:s'),
        'event' => $event,
        'hp_masked' => $hp !== '' ? aicc_auth_mask_hp($hp) : '',
        'hp_last4' => $hp !== '' ? substr($hp, -4) : '',
        'ip' => cs_client_ip(),
        'ua' => substr((string)($_SERVER['HTTP_USER_AGENT'] ?? ''), 0, 180),
    ];
    @file_put_contents(aicc_auth_log_file(), json_encode($row, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES) . "\n", FILE_APPEND | LOCK_EX);
}

function aicc_auth_read_logs(int $limit = 50): array {
    $file = aicc_auth_log_file();
    if (!is_file($file)) return [];
    $lines = @file($file, FILE_IGNORE_NEW_LINES | FILE_SKIP_EMPTY_LINES);
    if (!is_array($lines)) return [];
    $lines = array_slice($lines, -1 * max(1, min($limit, 200)));
    $items = [];
    foreach (array_reverse($lines) as $line) {
        $j = json_decode($line, true);
        if (is_array($j)) $items[] = $j;
    }
    return $items;
}

function aicc_auth_ok(): bool {
    return !empty($_SESSION['aicc_auth_ok']) && !empty($_SESSION['aicc_auth_hp'])
        && !empty($_SESSION['aicc_auth_at']) && (time() - (int)$_SESSION['aicc_auth_at'] < 43200);
}

cs_bootstrap(['GET', 'POST']);
aicc_auth_session_start();

$action = $_GET['action'] ?? 'status';
$cfg = aicc_auth_load_cfg();
$allowed = aicc_auth_allowed_numbers($cfg);

if ($action === 'status') {
    $ok = aicc_auth_ok();
    cs_json_ok([
        'authenticated' => $ok,
        'verifiedHp' => $ok ? aicc_auth_mask_hp((string)$_SESSION['aicc_auth_hp']) : '',
        'hasAllowedNumbers' => count($allowed) > 0,
    ]);
}

if ($action === 'log') {
    if (!aicc_auth_ok()) cs_json_error('aicc_unauthorized', 401);
    $limit = (int)($_GET['limit'] ?? 50);
    cs_json_ok(['items' => aicc_auth_read_logs($limit)]);
}

if ($action === 'setup') {
    if (count($allowed) > 0) cs_json_error('이미 허용 번호가 등록되어 있습니다.', 409);
    $password = (string)($_POST['password'] ?? '');
    $hp = cs_normalize_hp((string)($_POST['hp'] ?? ''));
    if (empty($cfg['password_hash']) || !password_verify($password, (string)$cfg['password_hash'])) {
        usleep(400000);
        cs_json_error('통합설정 비밀번호가 올바르지 않습니다.', 403);
    }
    if (!cs_is_valid_hp($hp)) cs_json_error('휴대폰 번호 형식이 올바르지 않습니다.', 422);
    $cfg['aicc_auth_phones'] = $hp;
    $cfg['updated_at'] = date('Y-m-d H:i:s');
    if (!aicc_auth_save_cfg($cfg)) cs_json_error('허용 번호 저장 실패', 500);
    cs_log('aicc_auth_setup', ['hp' => substr($hp, -4), 'ip' => cs_client_ip()]);
    aicc_auth_log_event('setup_allowed_phone', $hp);
    cs_json_ok(['message' => '허용 번호가 등록되었습니다.', 'verifiedHp' => aicc_auth_mask_hp($hp)]);
}

if ($action === 'send') {
    $hp = cs_normalize_hp((string)($_POST['hp'] ?? ''));
    if (!cs_is_valid_hp($hp)) cs_json_error('휴대폰 번호 형식이 올바르지 않습니다.', 422);
    if (count($allowed) === 0) cs_json_error('통합설정에 허용된 인증 번호가 없습니다.', 403, ['needSetup' => true]);
    if (!in_array($hp, $allowed, true)) {
        cs_log('aicc_auth_denied', ['hp' => substr($hp, -4), 'ip' => cs_client_ip()]);
        cs_json_error('접속이 허용된 번호가 아닙니다.', 403);
    }

    $db = cs_db();
    $stmt = $db->prepare("SELECT id FROM cs_sms_codes WHERE hp = ? AND created_at > (NOW() - INTERVAL 60 SECOND) LIMIT 1");
    $stmt->bind_param('s', $hp);
    $stmt->execute();
    $exist = $stmt->get_result()->fetch_assoc();
    $stmt->close();
    if ($exist) {
        cs_close();
        cs_json_error('인증번호 발송 후 60초 뒤에 재요청해 주세요.', 429);
    }

    $code = str_pad((string)random_int(0, 999999), 6, '0', STR_PAD_LEFT);
    $expiresAt = date('Y-m-d H:i:s', time() + 180);
    $ip = cs_client_ip();
    $name = 'AICC 관리자';
    $now = date('Y-m-d H:i:s');
    $stmt = $db->prepare("INSERT INTO cs_sms_codes (hp, code, expires_at, ip, name, created_at) VALUES (?, ?, ?, ?, ?, ?)");
    $stmt->bind_param('ssssss', $hp, $code, $expiresAt, $ip, $name, $now);
    if (!$stmt->execute()) {
        cs_close();
        cs_json_error('인증번호 저장 실패', 500);
    }
    $stmt->close();

    $sent = cs_sms_send_auth($hp, $code);
    if (empty($sent['ok'])) {
        cs_log('aicc_auth_sms_fail', ['hp' => substr($hp, -4), 'error' => $sent['error'] ?? 'unknown']);
        cs_close();
        cs_json_error('SMS 발송 실패: ' . ($sent['error'] ?? '알 수 없음'), 500);
    }

    cs_log('aicc_auth_sms_send', ['hp' => substr($hp, -4), 'ip' => $ip]);
    cs_close();
    cs_json_ok(['expiresIn' => 180, 'message' => '인증번호가 발송되었습니다.']);
}

if ($action === 'verify') {
    $hp = cs_normalize_hp((string)($_POST['hp'] ?? ''));
    $code = preg_replace('/[^0-9]/', '', (string)($_POST['code'] ?? ''));
    if (!cs_is_valid_hp($hp)) cs_json_error('휴대폰 번호 형식이 올바르지 않습니다.', 422);
    if (strlen($code) !== 6) cs_json_error('인증번호 6자리를 입력해주세요.', 422);
    if (count($allowed) === 0 || !in_array($hp, $allowed, true)) {
        cs_json_error('접속이 허용된 번호가 아닙니다.', 403);
    }

    $db = cs_db();
    $stmt = $db->prepare("
        SELECT id, code, expires_at, attempts
          FROM cs_sms_codes
         WHERE hp = ? AND verified = 0
         ORDER BY id DESC LIMIT 1
    ");
    $stmt->bind_param('s', $hp);
    $stmt->execute();
    $row = $stmt->get_result()->fetch_assoc();
    $stmt->close();

    if (!$row) {
        cs_close();
        cs_json_error('발송된 인증번호가 없습니다. 다시 요청해 주세요.', 422);
    }
    if (strtotime($row['expires_at']) < time()) {
        cs_close();
        cs_json_error('인증번호가 만료되었습니다. 다시 요청해 주세요.', 422, ['expired' => true]);
    }
    if ((int)$row['attempts'] >= 5) {
        cs_close();
        cs_json_error('시도 횟수 초과. 다시 요청해 주세요.', 429);
    }

    $up = $db->prepare("UPDATE cs_sms_codes SET attempts = attempts + 1 WHERE id = ?");
    $up->bind_param('i', $row['id']);
    $up->execute();
    $up->close();

    if ((string)$row['code'] !== $code) {
        cs_close();
        cs_json_error('인증번호가 일치하지 않습니다.', 422);
    }

    $up = $db->prepare("UPDATE cs_sms_codes SET verified = 1, verified_at = NOW() WHERE id = ?");
    $up->bind_param('i', $row['id']);
    $up->execute();
    $up->close();

    $_SESSION['aicc_auth_ok'] = true;
    $_SESSION['aicc_auth_hp'] = $hp;
    $_SESSION['aicc_auth_at'] = time();

    cs_log('aicc_auth_verify_ok', ['hp' => substr($hp, -4), 'ip' => cs_client_ip()]);
    aicc_auth_log_event('login', $hp);
    cs_close();
    cs_json_ok(['verifiedHp' => aicc_auth_mask_hp($hp)]);
}

if ($action === 'logout') {
    $logoutHp = (string)($_SESSION['aicc_auth_hp'] ?? '');
    if ($logoutHp !== '') aicc_auth_log_event('logout', $logoutHp);
    $_SESSION = [];
    if (ini_get('session.use_cookies')) {
        $p = session_get_cookie_params();
        setcookie(session_name(), '', time() - 42000, $p['path'], $p['domain'], $p['secure'], $p['httponly']);
    }
    session_destroy();
    cs_json_ok(['message' => 'logged out']);
}

cs_json_error('unknown action', 404);
