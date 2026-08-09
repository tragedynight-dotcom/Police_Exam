<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
  <title>실무역량평가DaMoa</title>
  <link rel="manifest" href="manifest.json">
  <link rel="apple-touch-icon" href="icon-512.png">
  <meta name="theme-color" content="#0b2a4a">
  <style>
    body, html {
      margin: 0; padding: 0; width: 100%; height: 100%;
      overflow: hidden; background-color: #ffffff;
      overscroll-behavior-y: contain; 
    }
    iframe {
      width: 100%; height: 100%; border: none;
    }
    /* 인앱 브라우저(네이버/카카오) 안내 배너 디자인 */
    #inapp-guide {
      display: none; position: fixed; bottom: 0; left: 0; width: 100%;
      background-color: #e63946; color: #ffffff; text-align: center;
      padding: 16px 20px; box-sizing: border-box; font-family: "Noto Sans KR", sans-serif;
      font-size: 0.95rem; line-height: 1.5; z-index: 9999;
      box-shadow: 0 -4px 12px rgba(0,0,0,0.2);
    }
    #inapp-guide b { color: #fde047; font-weight: 800; }
  </style>
</head>
<body>
  <!-- 동적 iframe (토큰 유지 기능 포함) -->
  <iframe id="app-iframe" allow="clipboard-write; camera; geolocation; fullscreen"></iframe>
  
  <!-- 네이버/카카오 접속 시 하단에 뜨는 안내 배너 (요청하신 문구 반영) -->
  <div id="inapp-guide">
    현재 네이버/카카오 앱에서는 단독 앱 설치가 제한됩니다.<br>
    우측 하단 메뉴(≡)에서 <b>'다른 브라우저로 열기'</b> 또는 <b>'홈 화면에 추가'</b>를 선택하여 사용해 주시기 바랍니다.
  </div>

  <script>
    // 브라우저 기억장치(localStorage)에서 로그인 토큰을 불러와 유지합니다.
    var token = localStorage.getItem('damoa_auth');
    var targetUrl = "https://police-exam.streamlit.app/?embed=true";
    
    var params = new URLSearchParams(window.location.search);
    var urlAuth = params.get('auth');
    if (urlAuth) {
      token = urlAuth;
      localStorage.setItem('damoa_auth', urlAuth);
    }
    
    if (token) {
      targetUrl += "&auth=" + token;
    }
    document.getElementById('app-iframe').src = targetUrl;

    // 접속한 앱이 네이버, 카카오톡, 라인인지 자동으로 감지합니다.
    var ua = navigator.userAgent.toLowerCase();
    if(ua.indexOf('naver') > -1 || ua.indexOf('kakaotalk') > -1 || ua.indexOf('line') > -1) {
      document.getElementById('inapp-guide').style.display = 'block';
    }

    // 서비스 워커 등록 (앱 설치용)
    if ('serviceWorker' in navigator) {
      window.addEventListener('load', () => {
        navigator.serviceWorker.register('sw.js').then(reg => {
          console.log('ServiceWorker 등록 성공');
        });
      });
    }
  </script>
</body>
</html>
