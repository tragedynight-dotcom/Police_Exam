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
  <iframe id="app-iframe" allow="clipboard-write; camera; geolocation; fullscreen"></iframe>
  
  <div id="inapp-guide">
    현재 네이버/카카오 앱에서는 단독 앱 설치가 제한됩니다.<br>
    우측 하단 메뉴(≡)에서 <b>'다른 브라우저로 열기'</b> 또는 <b>'홈 화면에 추가'</b>를 선택하여 사용해 주시기 바랍니다.
  </div>

  <script>
    try {
      var token = null;
      try { token = localStorage.getItem('damoa_auth'); } catch(e) {}
      
      var targetUrl = "https://police-exam.streamlit.app/?embed=true";
      var params = new URLSearchParams(window.location.search);
      var urlAuth = params.get('auth');
      
      if (urlAuth) {
        token = urlAuth;
        try { localStorage.setItem('damoa_auth', urlAuth); } catch(e) {}
      }
      
      if (token) {
        targetUrl += "&auth=" + token;
      }
      
      var iframe = document.getElementById('app-iframe');
      if (iframe) {
        iframe.src = targetUrl;
      }
    } catch (err) {
      var iframe = document.getElementById('app-iframe');
      if (iframe) {
        iframe.src = "https://police-exam.streamlit.app/?embed=true";
      }
    }

    var ua = navigator.userAgent.toLowerCase();
    if(ua.indexOf('naver') > -1 || ua.indexOf('kakaotalk') > -1 || ua.indexOf('line') > -1) {
      var guide = document.getElementById('inapp-guide');
      if(guide) guide.style.display = 'block';
    }

    if ('serviceWorker' in navigator) {
      window.addEventListener('load', () => {
        navigator.serviceWorker.register('sw.js').catch(() => {});
      });
    }
  </script>
</body>
</html>
