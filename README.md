# X-unit Portfolio

업무에서 직접 겪은 문제를 풀기 위해 만든 프로젝트를 모아 둔 저장소입니다.
**프로젝트마다 브랜치가 따로 있습니다.** 아래 표에서 프로젝트 이름을 누르면 해당 브랜치로 이동합니다.

*A collection of projects built to solve problems I ran into at work. Each project lives in its own branch.*

## 프로젝트

| 프로젝트 | 한 줄 소개 | 기술 | 브랜치 |
|---|---|---|---|
| [**메일 백업 검색기**](https://github.com/wyszkd93/X-unit-portfolio/tree/EMAIL) | 매달 zip 으로 받는 메일 백업을 압축을 풀지 않고 검색하는 Windows 로컬 웹 앱 | Python 표준 라이브러리, SQLite, HTML/JS, PowerShell | [`EMAIL`](https://github.com/wyszkd93/X-unit-portfolio/tree/EMAIL) |

---

### 메일 백업 검색기 · [`EMAIL`](https://github.com/wyszkd93/X-unit-portfolio/tree/EMAIL)

[![메일 백업 검색기](https://raw.githubusercontent.com/wyszkd93/X-unit-portfolio/EMAIL/docs/main.png)](https://github.com/wyszkd93/X-unit-portfolio/tree/EMAIL)

- **문제**: 보안 정책상 지난 메일은 매달 zip 백업으로만 받아서, 메일 하나를 찾으려면 여러 달의 압축을 풀어 뒤져야 했습니다.
- **해결**: zip(중첩 zip 포함)을 풀지 않고 색인해 Outlook 같은 화면에서 바로 검색. 외부 통신 없이 내 PC 에서만 동작합니다.
- **포인트**: 외부 라이브러리 0개 · 설치 없이 더블클릭 배포 · 로컬 웹 앱 보안 설계(CSRF·DNS 리바인딩·CSP) · 새 백업 자동 반영
- **문서**: [README](https://github.com/wyszkd93/X-unit-portfolio/blob/EMAIL/README.md) · [English](https://github.com/wyszkd93/X-unit-portfolio/blob/EMAIL/README.en.md) · [프로젝트 소개서 PDF](https://github.com/wyszkd93/X-unit-portfolio/blob/EMAIL/docs/%EB%A9%94%EC%9D%BC%EB%B0%B1%EC%97%85%EA%B2%80%EC%83%89%EA%B8%B0_%ED%94%84%EB%A1%9C%EC%A0%9D%ED%8A%B8%EC%86%8C%EA%B0%9C.pdf)

> 화면은 모두 가상의 회사·사람·메일로 만든 데모 데이터입니다.
