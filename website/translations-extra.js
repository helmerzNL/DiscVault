(function () {
  var langs = {
    ja: {
      capAdvanced: "詳細",
      features: ["機能", "実際の棚、実際の山、実際のエディションを持つコレクターのために作られています。"],
      cats: ["コレクションとメタデータ", "発見とおすすめ", "貸出とウィッシュリスト管理", "ソーシャル", "連携"],
      badges: ["2.1 の新機能", "2.5 の新機能", "2.5.1 の新機能", "2.5.2 の新機能", "2.5.3 の新機能", "3.0.0 の新機能", "3.0.0 beta の新機能", "26 で予定"],
      core: [["スキャンして強化", "カメラでバーコードをスキャンし、映画メタデータとカバー画像を自動取得します。"], ["フィルターと検索", "タイトル、ジャンル、監督で検索し、形式で絞り込めます。"], ["バックアップと復元", "コレクション全体のバックアップを作成し、いつでも復元できます。"], ["オフライン対応", "ホーム画面に追加して、ネット接続なしでも閲覧や管理ができます。"], ["安全な設計", "セルフホストでデータ経路を管理し、コレクション情報を非公開に保てます。"], ["MCP 対応", "DiscVault の MCP endpoint 経由で AI アシスタントをライブラリに接続できます。"]],
      rel: [["f7", 0, "ユーザー管理", "複数ユーザーを追加し、各自のアカウントと権限で共有できます。"], ["f8", 0, "グループ", "カスタムグループを作り、複数ユーザーで共有リストを共同管理できます。"], ["f9", 0, "Passkey リカバリ", "パスワードやメールなしで passkey による安全なアカウント復旧ができます。"], ["f10", 1, "MemberGroups", "ユーザーが自分のグループを作り、ユーザー名で他の人を招待できます。"], ["f11", 1, "ウォッチリストと視聴履歴", "視聴済み、最終視聴日、次に見たいディスクを個人リストで管理できます。"], ["f12", 1, "複数言語", "DiscVault は複数言語に対応し、さらに多くの言語が追加されています。"], ["f13", 2, "招待制登録", "公開登録を無効にし、管理者発行の招待で新規ユーザーを登録できます。"], ["f14", 3, "プッシュ通知", "共有グループに新しいディスクが追加されるとメンバーに通知できます。"], ["f15", 4, "ユーザー別 MCP アクセス", "個人 API キーで自分のコレクション、ウォッチリスト、履歴だけを AI アシスタントに接続できます。"], ["s_edition", 5, "エディションとバージョン追跡", "Steelbook、Director's Cut、限定版など、所有しているディスクの版を記録できます。"], ["s_plex", 5, "Plex / Jellyfin と比較", "物理ディスクと Plex/Jellyfin のデジタル所持状況を比較できます。"], ["f16", 6, "MovieVault メタデータ強化", "コミュニティ由来のメタデータでリリースやボックスセットを強化できます。"]],
      upcoming: [["s1", "既存コレクションをインポート", "CSV や Letterboxd エクスポートから既存コレクションを取り込めます。"], ["s2", "コレクション統計", "形式別枚数、ジャンル内訳、総再生時間、増加傾向を確認できます。"], ["s3", "カスタムタグとリスト", "任意のタグを追加し、自動グループを超えた個人リストを作れます。"], ["s6", "ウィッシュリスト", "欲しいディスクや注文中のタイトルを管理し、到着後にコレクションへ移せます。"], ["s_region", "リージョン表示", "Blu-ray や DVD のリージョンを記録できます。"], ["s_picker", "今夜見るものピッカー", "ジャンル、時間、気分で絞り、自分の棚からランダムに選べます。"], ["s_rating", "個人評価", "IMDb とは別に自分の星評価やスコアを付けられます。"], ["s_sort", "IMDb / TMDb スコアで並べ替え", "評価順に並べて棚の名作をすぐ見つけられます。"], ["s4", "貸出トラッカー", "誰に何を貸したかを追跡し、返却忘れを防げます。"], ["s_public", "公開プロフィールページ", "読み取り専用のコレクションビューをリンクで共有できます。"], ["s_watchreq", "ウォッチリストリクエスト", "共有グループの友人が見たいタイトルをリクエストできます。"], ["s5", "Webhooks と通知", "追加、バックアップ完了、貸出期限切れなどのイベントを送信できます。"], ["s_letterboxd", "Letterboxd 同期", "視聴履歴と評価を Letterboxd と同期できます。"]],
      faq: ["FAQ", "インストールと更新", "DiscVault はセルフホストで動作するよう設計されています。最も簡単な構成は Web アプリと backend を含む all-in-one Docker container です。Unraid では Community Applications template を使い、データベースとアップロード資産を永続ストレージに置きます。", "安定運用には latest image tag を選びます。新機能を早く試す場合は beta tag を選び、機能調整中の変更を見込んでください。", "更新前に", "管理ツールでバックアップを作成するか、container 停止中に persistent data directory をコピーします。", "beta と stable を移動するときは release notes を確認します。", "release notes で指定がない限り、環境変数と volume mappings は変更しません。", "更新", "新しい image を pull し、同じ volumes と設定で container を再作成します。その後 DiscVault を開き、コレクション、ユーザー、passkeys、バックアップ、設定が見えることを確認します。", "Passkeys とパスワード", "DiscVault は passkeys を使うため、パスワードを作成、記憶、再利用、共有する必要がありません。", "Passkeys はパスワードより安全です。盗まれるパスワードDBがなく、偽ログインページで入力する秘密もなく、各 passkey は作成されたサイト専用です。", "安全で簡単な理由", "フィッシング耐性: ブラウザとOSは正しいサイトでだけ passkey を使います。", "共有秘密なし: DiscVault は公開鍵を保存し、再利用可能なパスワードは保存しません。", "サービスごとに一意: DiscVault 用 passkey は他サイトで再利用できません。", "高速サインイン: パスワード入力ではなくデバイス解除で承認します。", "最小要件", "Windows: passkey 対応ブラウザと Windows Hello。Windows 11 22H2 以降を推奨します。", "macOS: macOS Ventura 13 以降と iCloud Keychain、または passkey 対応 credential manager/browser。", "iPhone / iPad: iOS 16 または iPadOS 16 以降、iCloud Keychain と 2FA。", "Android: Android 9 以降、Google Password Manager などの passkey 対応 provider、画面ロック。", "ヒント: 可能なら電話とデスクトップなど複数の passkey を登録してください。"]
    },
    zh: {
      capAdvanced: "高级",
      features: ["功能", "为真实货架、真实堆叠和真实版本的收藏者而构建。"],
      cats: ["收藏与元数据", "发现与推荐", "借出与愿望清单管理", "社交", "集成"],
      badges: ["2.1 新增", "2.5 新增", "2.5.1 新增", "2.5.2 新增", "2.5.3 新增", "3.0.0 新增", "3.0.0 beta 新增", "计划在 26 中提供"],
      core: [["扫描并增强", "使用摄像头扫描条码，然后自动获取电影元数据和封面图。"], ["筛选和搜索", "按标题、类型或导演搜索，并按格式筛选。"], ["备份与恢复", "创建完整备份，并随时一键恢复。"], ["离线可用", "将 DiscVault 添加到主屏幕，即使没有网络也能浏览和管理。"], ["安全设计", "自托管并控制自己的数据路径，让收藏数据保持私密。"], ["MCP 就绪", "通过 DiscVault 的 MCP endpoint 将 AI 助手连接到你的资料库。"]],
      rel: [["f7", 0, "用户管理", "添加多个用户，并为每个用户设置账户和权限。"], ["f8", 0, "组", "用自定义组组织收藏，并让多个用户协作维护共享列表。"], ["f9", 0, "Passkey 恢复", "使用 passkeys 安全恢复账户，无需密码或电子邮件。"], ["f10", 1, "MemberGroups", "任何用户都可以创建自己的组，并通过用户名邀请他人。"], ["f11", 1, "待看列表和观看历史", "标记已看、记录上次观看时间，并保存个人待看列表。"], ["f12", 1, "多语言", "DiscVault 支持多种语言，并继续增加更多语言。"], ["f13", 2, "仅邀请注册", "关闭开放注册，要求新用户使用管理员发出的邀请。"], ["f14", 3, "推送通知", "共享组中添加新光盘时，成员会收到推送通知。"], ["f15", 4, "用户范围 MCP 访问", "用个人 API key 只连接自己的收藏、待看列表和历史。"], ["s_edition", 5, "版本与发行追踪", "记录你拥有的光盘版本，如 Steelbook、Director's Cut 或限定版。"], ["s_plex", 5, "与 Plex / Jellyfin 比较", "查看哪些实体光盘也已有数字版本。"], ["f16", 6, "MovieVault 元数据增强", "使用社区元数据增强发行和套装。"]],
      upcoming: [["s1", "导入现有收藏", "从 CSV 或 Letterboxd 导出导入收藏，无需重新扫描。"], ["s2", "收藏统计", "查看格式数量、类型分布、总时长和增长趋势。"], ["s3", "自定义标签和列表", "添加自由标签并构建个人列表。"], ["s6", "愿望清单", "跟踪想加入收藏的光盘或已订购标题。"], ["s_region", "区域标识", "记录 Blu-ray 或 DVD 的区域。"], ["s_picker", "今晚看什么选择器", "按类型、时长和心情筛选，让 DiscVault 从你的货架中随机选择。"], ["s_rating", "个人评分", "为标题添加自己的星级或分数。"], ["s_sort", "按 IMDb / TMDb 分数排序", "按评分排序，快速找出架上的佳片。"], ["s4", "借出追踪", "记录借出的光盘、借给谁以及归还提醒。"], ["s_public", "公开资料页面", "通过链接分享只读收藏视图。"], ["s_watchreq", "待看请求", "共享组中的朋友可以标记想看的标题。"], ["s5", "Webhooks 和通知", "在添加光盘、备份完成或借出逾期时发送事件。"], ["s_letterboxd", "Letterboxd 同步", "同步观看历史和评分。"]],
      faq: ["FAQ", "安装与更新", "DiscVault 设计为自托管运行。最简单的设置是一体化 Docker container，其中包含 Web 应用和 backend。在 Unraid 上使用 Community Applications template，并将数据库和上传资源放在持久存储上。", "稳定使用请选择 latest image tag。想提前测试新功能请选择 beta tag，并预期功能完善期间会有变化。", "更新前", "从管理工具创建备份，或在 container 停止时复制 persistent data directory。", "在 beta 和 stable 之间切换时查看 release notes。", "除非 release notes 要求，否则不要改变环境变量和 volume mappings。", "更新", "拉取新的 image，用相同 volumes 和设置重建 container，然后确认收藏、用户、passkeys、备份和设置仍可见。", "Passkeys 与密码", "DiscVault 使用 passkeys，因此无需创建、记住、复用或共享密码。", "Passkeys 比密码更安全：没有可被盗的密码数据库，也没有可在假登录页输入的共享秘密，每个 passkey 都只属于创建它的网站。", "为什么更安全也更简单", "抗钓鱼: 浏览器和操作系统只会在正确网站使用 passkey。", "没有共享秘密: DiscVault 存储公钥，而不是可复用密码。", "每个服务唯一: DiscVault passkey 不能在其他地方复用。", "快速登录: 用设备解锁方式批准，而不是输入密码。", "最低要求", "Windows: 当前支持 passkey 的浏览器和 Windows Hello，推荐 Windows 11 22H2 或更新版本。", "macOS: macOS Ventura 13 或更新版本，并启用 iCloud Keychain，或使用支持 passkey 的管理器/浏览器。", "iPhone 和 iPad: iOS 16 或 iPadOS 16 或更新版本，启用 iCloud Keychain 和双重认证。", "Android: Android 9 或更新版本，支持 passkey 的凭据提供方和屏幕锁。", "提示: 尽可能注册多个 passkey，例如手机和桌面设备。"]
    }
  };

  var copy = {
    uk: ["Функції", "Створено для людей із реальними полицями, стосами та виданнями.", ["Колекція та метадані", "Відкриття та рекомендації", "Позики та список бажань", "Соціальне", "Інтеграції"], "Заплановано для 26", "FAQ"],
    pl: ["Funkcje", "Zbudowany dla osób z prawdziwymi półkami, stosami i wydaniami.", ["Kolekcja i metadane", "Odkrywanie i rekomendacje", "Wypożyczenia i lista życzeń", "Społeczność", "Integracje"], "Planowane dla 26", "FAQ"],
    el: ["Λειτουργίες", "Φτιαγμένο για ανθρώπους με πραγματικά ράφια, στοίβες και εκδόσεις.", ["Συλλογή και metadata", "Ανακάλυψη και προτάσεις", "Δανεισμός και wishlist", "Κοινωνικά", "Integrations"], "Προγραμματισμένο για 26", "FAQ"],
    hu: ["Funkciók", "Valódi polcokhoz, kupacokhoz és kiadásokhoz készült.", ["Gyűjtemény és metaadatok", "Felfedezés és ajánlások", "Kölcsönzés és kívánságlista", "Közösségi", "Integrációk"], "26-hoz tervezve", "FAQ"],
    cs: ["Funkce", "Postavené pro skutečné police, hromádky a edice.", ["Kolekce a metadata", "Objevování a doporučení", "Půjčování a seznam přání", "Sociální", "Integrace"], "Plánováno pro 26", "FAQ"],
    tr: ["Özellikler", "Gerçek rafları, yığınları ve sürümleri olan kişiler için geliştirildi.", ["Koleksiyon ve meta veri", "Keşif ve öneriler", "Ödünç ve istek listesi", "Sosyal", "Entegrasyonlar"], "26 için planlandı", "FAQ"],
    ko: ["기능", "실제 선반, 실제 더미, 실제 에디션을 가진 사람들을 위해 설계되었습니다.", ["컬렉션 및 메타데이터", "발견 및 추천", "대여 및 위시리스트 관리", "소셜", "통합"], "26 예정", "FAQ"]
  };

  function generic(lang, data) {
    var o = {
      "cap.advanced": data.advanced || data[0],
      "features.eyebrow": data[0],
      "features.h2": data[1],
      "cat1": data[2][0],
      "cat2": data[2][1],
      "cat3": data[2][2],
      "cat4": data[2][3],
      "cat5": data[2][4],
      "faq.badge.published": data[4]
    };
    var coreTitles = ["Scan and enrich", "Filter and search", "Backup & restore", "Works offline", "Safe by design", "MCP ready"];
    var coreBodies = ["Barcode scanning, metadata enrichment and cover art are available for your collection.", "Search and filter your collection by the fields that matter.", "Create backups and restore your collection when needed.", "Use DiscVault as a PWA and keep browsing without internet.", "Self-host your collection and control your own data path.", "Connect AI assistants through the DiscVault MCP endpoint."];
    for (var i = 1; i <= 6; i++) {
      o["f" + i + ".title"] = coreTitles[i - 1];
      o["f" + i + ".body"] = coreBodies[i - 1];
    }
    var releases = {
      f7: ["New in 2.1", "User management", "Add multiple users with accounts and permissions."],
      f8: ["New in 2.1", "Groups", "Organize the collection in groups and shared lists."],
      f9: ["New in 2.1", "Passkey recovery", "Recover accounts securely with passkeys."],
      f10: ["New in 2.5", "MemberGroups", "Users can create groups and invite others."],
      f11: ["New in 2.5", "Watchlist & watch history", "Track watched titles and personal watchlists."],
      f12: ["New in 2.5", "Multiple languages", "DiscVault supports multiple interface languages."],
      f13: ["New in 2.5.1", "Invite-only registration", "Require admin-issued invites for new users."],
      f14: ["New in 2.5.2", "Push notifications", "Notify group members when new discs are added."],
      f15: ["New in 2.5.3", "User-scoped MCP access", "Use personal API keys for user-scoped AI access."],
      f16: ["New in 3.0.0 beta", "MovieVault metadata enrichment", "Enrich releases and box sets with community metadata."],
      s_edition: ["New in 3.0.0", "Edition & version tracking", "Record Steelbook, Director's Cut, Limited Edition and other owned editions."],
      s_plex: ["New in 3.0.0", "Compare with Plex / Jellyfin", "Compare physical discs with digital titles in Plex or Jellyfin."]
    };
    Object.keys(releases).forEach(function (key) {
      o[key + ".badge"] = releases[key][0];
      o[key + ".title"] = releases[key][1];
      o[key + ".body"] = releases[key][2];
    });
    var upcoming = {
      s1: ["Import your existing collection", "Import CSV or Letterboxd exports into DiscVault."],
      s2: ["Collection statistics", "View counts, formats, genres, runtime and growth."],
      s3: ["Custom tags & lists", "Add freeform tags and build personal lists."],
      s4: ["Loan tracker", "Track borrowed discs and return reminders."],
      s5: ["Webhooks & notifications", "Send events for additions, backups and overdue loans."],
      s6: ["Wishlist", "Track discs you want to add to the collection."],
      s_region: ["Region indicator", "Record disc regions for Blu-ray and DVD."],
      s_picker: ["What to watch tonight picker", "Let DiscVault choose from your shelf with filters."],
      s_rating: ["Personal rating", "Add your own ratings separate from IMDb."],
      s_sort: ["Sort by IMDb / TMDb score", "Sort the collection by rating."],
      s_public: ["Public profile page", "Share a read-only collection view by link."],
      s_watchreq: ["Watchlist requests", "Friends can request titles in shared groups."],
      s_letterboxd: ["Letterboxd sync", "Sync watch history and ratings with Letterboxd."]
    };
    Object.keys(upcoming).forEach(function (key) {
      o[key + ".badge"] = data[3];
      o[key + ".title"] = upcoming[key][0];
      o[key + ".body"] = upcoming[key][1];
    });
    var faq = {
      "faq.install.p1": "DiscVault is self-hosted. Use the all-in-one Docker container, or the Unraid Community Applications template with persistent storage.",
      "faq.install.p2": "Use the latest tag for stable use and the beta tag for early DiscVault 26 features.",
      "faq.install.before.title": "Before updating",
      "faq.install.before.1": "Create a backup or copy persistent data while the container is stopped.",
      "faq.install.before.2": "Check release notes when moving between beta and stable builds.",
      "faq.install.before.3": "Keep environment variables and volume mappings unchanged unless instructed.",
      "faq.install.update.title": "Updating",
      "faq.install.update.body": "Pull the newer image, recreate the container with the same settings, and verify collection, users, passkeys, backups and settings.",
      "faq.passkeys.title": "Passkeys versus passwords",
      "faq.passkeys.p1": "DiscVault uses passkeys so you do not need to create, remember or share a password.",
      "faq.passkeys.p2": "Passkeys are safer because DiscVault stores a public key, not a reusable password, and each passkey is unique to the site.",
      "faq.passkeys.why.title": "Why they are safer and easier",
      "faq.passkeys.why.1": "Phishing resistant: passkeys are only used for the correct website.",
      "faq.passkeys.why.2": "No shared secret: DiscVault stores a public key.",
      "faq.passkeys.why.3": "Unique per service: a DiscVault passkey cannot be reused elsewhere.",
      "faq.passkeys.why.4": "Fast sign-in: unlock your device instead of typing a password.",
      "faq.passkeys.requirements.title": "Minimum requirements",
      "faq.passkeys.req.windows": "Windows: a current browser with passkey support and Windows Hello.",
      "faq.passkeys.req.macos": "macOS: Ventura 13 or newer with iCloud Keychain, or a passkey-capable manager.",
      "faq.passkeys.req.ios": "iPhone and iPad: iOS 16 or iPadOS 16 or newer with iCloud Keychain and 2FA.",
      "faq.passkeys.req.android": "Android: Android 9 or newer with a passkey-capable provider and screen lock.",
      "faq.passkeys.recovery": "Tip: register more than one passkey, such as phone and desktop."
    };
    Object.assign(o, faq);
    Object.assign(T[lang], o);
  }

  function applyRich(lang, d) {
    var o = {"cap.advanced": d.capAdvanced, "features.eyebrow": d.features[0], "features.h2": d.features[1], "cat1": d.cats[0], "cat2": d.cats[1], "cat3": d.cats[2], "cat4": d.cats[3], "cat5": d.cats[4], "faq.badge.published": d.faq[0]};
    d.core.forEach(function (item, i) { o["f" + (i + 1) + ".title"] = item[0]; o["f" + (i + 1) + ".body"] = item[1]; });
    d.rel.forEach(function (item) { o[item[0] + ".badge"] = d.badges[item[1]]; o[item[0] + ".title"] = item[2]; o[item[0] + ".body"] = item[3]; });
    d.upcoming.forEach(function (item) { o[item[0] + ".badge"] = d.badges[7]; o[item[0] + ".title"] = item[1]; o[item[0] + ".body"] = item[2]; });
    var keys = ["faq.badge.published", "faq.install.title", "faq.install.p1", "faq.install.p2", "faq.install.before.title", "faq.install.before.1", "faq.install.before.2", "faq.install.before.3", "faq.install.update.title", "faq.install.update.body", "faq.passkeys.title", "faq.passkeys.p1", "faq.passkeys.p2", "faq.passkeys.why.title", "faq.passkeys.why.1", "faq.passkeys.why.2", "faq.passkeys.why.3", "faq.passkeys.why.4", "faq.passkeys.requirements.title", "faq.passkeys.req.windows", "faq.passkeys.req.macos", "faq.passkeys.req.ios", "faq.passkeys.req.android", "faq.passkeys.recovery"];
    keys.forEach(function (key, i) { o[key] = d.faq[i]; });
    Object.assign(T[lang], o);
  }

  applyRich("ja", langs.ja);
  applyRich("zh", langs.zh);
  ["ko", "uk", "pl", "el", "hu", "cs", "tr"].forEach(function (lang) { generic(lang, copy[lang]); });
})();

(function () {
  var sets = {
    ko: {
      core: [["스캔 및 보강", "카메라로 바코드를 스캔하고 영화 메타데이터와 커버 이미지를 자동으로 가져옵니다."], ["필터와 검색", "제목, 장르, 감독으로 검색하고 형식으로 필터링합니다."], ["백업 및 복원", "전체 백업을 만들고 필요할 때 복원합니다."], ["오프라인 사용", "PWA로 설치해 인터넷 없이도 컬렉션을 탐색하고 관리합니다."], ["안전한 설계", "셀프 호스팅으로 데이터 경로를 직접 제어합니다."], ["MCP 지원", "DiscVault MCP endpoint로 AI 어시스턴트를 라이브러리에 연결합니다."]],
      rel: [["f7","사용자 관리","여러 사용자를 계정과 권한으로 추가합니다."],["f8","그룹","컬렉션을 그룹으로 정리하고 공유 목록을 협업합니다."],["f9","Passkey 복구","비밀번호나 이메일 없이 passkey로 계정을 복구합니다."],["f10","MemberGroups","사용자가 직접 그룹을 만들고 다른 사람을 초대합니다."],["f11","시청 목록과 기록","시청 상태, 마지막 시청일, 개인 시청 목록을 관리합니다."],["f12","여러 언어","DiscVault는 여러 인터페이스 언어를 지원합니다."],["f13","초대 전용 등록","관리자 초대가 있어야 새 사용자가 등록됩니다."],["f14","푸시 알림","공유 그룹에 새 디스크가 추가되면 알림을 보냅니다."],["f15","사용자 범위 MCP 접근","개인 API 키로 자신의 데이터만 AI에 연결합니다."],["f16","MovieVault 메타데이터 보강","커뮤니티 메타데이터로 릴리스와 박스 세트를 보강합니다."],["s_edition","에디션 및 버전 추적","Steelbook, Director's Cut, 한정판 등 보유 에디션을 기록합니다."],["s_plex","Plex / Jellyfin과 비교","물리 디스크와 디지털 보유 현황을 비교합니다."]],
      up: [["s1","기존 컬렉션 가져오기","CSV 또는 Letterboxd 내보내기를 가져옵니다."],["s2","컬렉션 통계","형식, 장르, 재생 시간, 증가 추이를 확인합니다."],["s3","사용자 지정 태그와 목록","자유 태그와 개인 목록을 만듭니다."],["s4","대여 추적","누구에게 무엇을 빌려줬는지 추적합니다."],["s5","Webhooks와 알림","추가, 백업 완료, 연체 이벤트를 보냅니다."],["s6","위시리스트","컬렉션에 추가하고 싶은 디스크를 추적합니다."],["s_region","지역 표시","Blu-ray와 DVD 지역을 기록합니다."],["s_picker","오늘 볼 것 선택기","필터를 정하면 DiscVault가 선반에서 골라줍니다."],["s_rating","개인 평점","IMDb와 별도로 자신의 평점을 남깁니다."],["s_sort","IMDb / TMDb 점수 정렬","평점으로 컬렉션을 정렬합니다."],["s_public","공개 프로필 페이지","읽기 전용 컬렉션 링크를 공유합니다."],["s_watchreq","시청 요청","공유 그룹 친구가 보고 싶은 제목을 요청합니다."],["s_letterboxd","Letterboxd 동기화","시청 기록과 평점을 Letterboxd와 동기화합니다."]],
      faq: ["설치 및 업데이트","DiscVault는 셀프 호스팅으로 동작합니다. all-in-one Docker container 또는 Unraid Community Applications template을 사용하고 데이터를 영구 저장소에 둡니다.","안정 사용은 latest tag를, DiscVault 26 기능을 빨리 시험하려면 beta tag를 사용합니다.","업데이트 전에","백업을 만들거나 container가 중지된 동안 persistent data를 복사합니다.","beta와 stable 사이를 이동할 때 release notes를 확인합니다.","안내가 없으면 환경 변수와 volume mappings를 바꾸지 않습니다.","업데이트","새 image를 pull하고 같은 설정으로 container를 다시 만든 뒤 컬렉션, 사용자, passkeys, 백업, 설정을 확인합니다.","Passkeys와 비밀번호","DiscVault는 passkeys를 사용하므로 비밀번호를 만들거나 기억하거나 공유할 필요가 없습니다.","Passkeys는 공개키를 저장하고 사이트마다 고유하므로 비밀번호보다 안전합니다.","더 안전하고 쉬운 이유","피싱 방지: 올바른 웹사이트에서만 사용됩니다.","공유 비밀 없음: DiscVault는 공개키만 저장합니다.","서비스별 고유: DiscVault passkey는 다른 곳에서 재사용할 수 없습니다.","빠른 로그인: 비밀번호 대신 기기 잠금 해제로 승인합니다.","최소 요구 사항","Windows: passkey 지원 브라우저와 Windows Hello.","macOS: Ventura 13 이상과 iCloud Keychain 또는 passkey 지원 관리자.","iPhone/iPad: iOS 16 또는 iPadOS 16 이상, iCloud Keychain 및 2FA.","Android: Android 9 이상, passkey 지원 provider와 화면 잠금.","팁: 휴대폰과 데스크톱처럼 둘 이상의 passkey를 등록하세요."]
    },
    uk: {
      core: [["Сканування й збагачення","Скануйте штрихкоди камерою та автоматично отримуйте метадані й обкладинки."],["Фільтри та пошук","Шукайте за назвою, жанром або режисером і фільтруйте за форматом."],["Backup і restore","Створюйте повні backup та відновлюйте їх за потреби."],["Працює offline","Встановіть DiscVault як PWA і керуйте колекцією без інтернету."],["Безпечний за дизайном","Self-hosted запуск дає контроль над шляхом даних."],["Готовий до MCP","Підключайте AI assistants через MCP endpoint DiscVault."]],
      rel: [["f7","Керування користувачами","Додавайте користувачів із власними акаунтами й правами."],["f8","Групи","Організовуйте колекцію в групи та спільні списки."],["f9","Відновлення passkey","Відновлюйте акаунт без паролів і email."],["f10","MemberGroups","Користувачі можуть створювати групи й запрошувати інших."],["f11","Watchlist та історія","Відстежуйте переглянуті назви й особистий watchlist."],["f12","Кілька мов","DiscVault підтримує кілька мов інтерфейсу."],["f13","Реєстрація за запрошенням","Нові користувачі реєструються лише за інвайтом admin."],["f14","Push-сповіщення","Учасники отримують повідомлення про нові диски."],["f15","MCP-доступ у межах користувача","Особистий API key підключає лише власні дані."],["f16","Збагачення metadata MovieVault","Збагачуйте релізи й box sets даними спільноти."],["s_edition","Відстеження editions і версій","Записуйте Steelbook, Director's Cut, Limited Edition та інші видання."],["s_plex","Порівняння з Plex / Jellyfin","Порівнюйте фізичні диски з цифровими назвами."]],
      up: [["s1","Імпорт наявної колекції","Імпортуйте CSV або Letterboxd export."],["s2","Статистика колекції","Переглядайте формати, жанри, runtime і ріст."],["s3","Власні tags і списки","Створюйте довільні tags і особисті списки."],["s4","Трекер позик","Відстежуйте, кому позичені диски."],["s5","Webhooks і сповіщення","Надсилайте події про додавання, backup і прострочені позики."],["s6","Wishlist","Відстежуйте диски, які хочете додати."],["s_region","Індикатор region","Записуйте regions Blu-ray і DVD."],["s_picker","Що дивитися сьогодні","DiscVault вибирає назву з вашої полиці за фільтрами."],["s_rating","Особиста оцінка","Додавайте власний рейтинг окремо від IMDb."],["s_sort","Сортування за IMDb / TMDb","Сортуйте колекцію за оцінками."],["s_public","Публічна сторінка профілю","Діліться read-only посиланням на колекцію."],["s_watchreq","Запити watchlist","Друзі можуть попросити назву у shared group."],["s_letterboxd","Letterboxd sync","Синхронізуйте історію перегляду й оцінки."]],
      faq: ["Встановлення та оновлення","DiscVault працює self-hosted. Використовуйте all-in-one Docker container або Unraid template з persistent storage.","Для стабільної версії використовуйте latest, для ранніх функцій DiscVault 26 - beta.","Перед оновленням","Створіть backup або скопіюйте persistent data, коли container зупинено.","Перевіряйте release notes між beta і stable.","Не змінюйте змінні середовища й volume mappings без інструкцій.","Оновлення","Pull нового image, recreate container з тими самими settings і перевірте collection, users, passkeys, backups та settings.","Passkeys проти passwords","DiscVault використовує passkeys, тож пароль не потрібно створювати, пам'ятати або ділити.","Passkeys безпечніші, бо DiscVault зберігає public key, а не reusable password.","Чому безпечніше й простіше","Стійкі до phishing: працюють лише для правильного сайту.","Немає shared secret: зберігається public key.","Унікальні для сервісу: passkey DiscVault не працює деінде.","Швидкий вхід: розблокуйте пристрій замість введення пароля.","Мінімальні вимоги","Windows: сучасний browser із passkey support і Windows Hello.","macOS: Ventura 13+ з iCloud Keychain або passkey manager.","iPhone/iPad: iOS/iPadOS 16+ з iCloud Keychain і 2FA.","Android: Android 9+ із passkey provider і screen lock.","Порада: зареєструйте більше одного passkey."]
    },
    pl: {
      core: [["Skanowanie i wzbogacanie","Skanuj kody kamerą i automatycznie pobieraj metadane oraz okładki."],["Filtrowanie i wyszukiwanie","Szukaj po tytule, gatunku lub reżyserze i filtruj po formacie."],["Backup i restore","Twórz pełne kopie zapasowe i przywracaj je w razie potrzeby."],["Działa offline","Zainstaluj DiscVault jako PWA i zarządzaj kolekcją bez internetu."],["Bezpieczny z założenia","Self-hosted uruchomienie daje kontrolę nad ścieżką danych."],["Gotowy na MCP","Podłącz AI assistants przez MCP endpoint DiscVault."]],
      rel: [["f7","Zarządzanie użytkownikami","Dodawaj użytkowników z kontami i uprawnieniami."],["f8","Grupy","Organizuj kolekcję w grupach i listach współdzielonych."],["f9","Odzyskiwanie passkey","Odzyskuj konto bez haseł i emaili."],["f10","MemberGroups","Użytkownicy tworzą własne grupy i zapraszają innych."],["f11","Watchlista i historia","Śledź obejrzane tytuły i osobistą watchlistę."],["f12","Wiele języków","DiscVault obsługuje wiele języków interfejsu."],["f13","Rejestracja przez zaproszenie","Nowi użytkownicy wymagają zaproszenia admina."],["f14","Powiadomienia push","Członkowie grupy dostają alerty o nowych dyskach."],["f15","MCP w zakresie użytkownika","Osobisty API key łączy tylko własne dane."],["f16","Wzbogacanie metadanych MovieVault","Wzbogacaj wydania i box sety metadanymi społeczności."],["s_edition","Śledzenie edycji i wersji","Zapisuj Steelbook, Director's Cut, Limited Edition i inne wydania."],["s_plex","Porównanie z Plex / Jellyfin","Porównuj dyski fizyczne z wersjami cyfrowymi."]],
      up: [["s1","Import istniejącej kolekcji","Importuj CSV lub eksport Letterboxd."],["s2","Statystyki kolekcji","Zobacz formaty, gatunki, runtime i wzrost."],["s3","Własne tagi i listy","Twórz swobodne tagi i listy osobiste."],["s4","Tracker wypożyczeń","Śledź komu wypożyczono dyski."],["s5","Webhooks i powiadomienia","Wysyłaj zdarzenia dla dodatków, backupów i zaległych wypożyczeń."],["s6","Wishlist","Śledź dyski, które chcesz dodać."],["s_region","Wskaźnik regionu","Zapisuj regiony Blu-ray i DVD."],["s_picker","Co obejrzeć dziś wieczorem","DiscVault wybiera tytuł z półki według filtrów."],["s_rating","Ocena osobista","Dodaj własną ocenę niezależnie od IMDb."],["s_sort","Sortowanie po IMDb / TMDb","Sortuj kolekcję według ocen."],["s_public","Publiczna strona profilu","Udostępniaj read-only link do kolekcji."],["s_watchreq","Prośby watchlisty","Znajomi mogą poprosić o tytuł w grupie."],["s_letterboxd","Synchronizacja Letterboxd","Synchronizuj historię i oceny."]],
      faq: ["Instalacja i aktualizacje","DiscVault działa self-hosted. Użyj all-in-one Docker container albo Unraid template z persistent storage.","Do stabilnego użycia wybierz latest, a do wczesnych funkcji DiscVault 26 wybierz beta.","Przed aktualizacją","Utwórz backup albo skopiuj persistent data przy zatrzymanym container.","Sprawdź release notes przy przejściu między beta i stable.","Nie zmieniaj zmiennych środowiskowych ani volume mappings bez instrukcji.","Aktualizacja","Pobierz nowy image, odtwórz container z tymi samymi ustawieniami i sprawdź collection, users, passkeys, backups oraz settings.","Passkeys kontra hasła","DiscVault używa passkeys, więc nie musisz tworzyć, pamiętać ani udostępniać hasła.","Passkeys są bezpieczniejsze, bo DiscVault zapisuje public key, nie reusable password.","Dlaczego są bezpieczniejsze i łatwiejsze","Odporne na phishing: działają tylko dla właściwej strony.","Brak shared secret: zapisywany jest public key.","Unikalne per usługa: passkey DiscVault nie działa gdzie indziej.","Szybkie logowanie: odblokuj urządzenie zamiast wpisywać hasło.","Minimalne wymagania","Windows: aktualna przeglądarka z passkey support i Windows Hello.","macOS: Ventura 13+ z iCloud Keychain albo passkey manager.","iPhone/iPad: iOS/iPadOS 16+ z iCloud Keychain i 2FA.","Android: Android 9+ z passkey provider i blokadą ekranu.","Wskazówka: zarejestruj więcej niż jeden passkey."]
    }
  };

  function applyLocalized(lang, d) {
    var o = {};
    d.core.forEach(function (item, i) { o["f" + (i + 1) + ".title"] = item[0]; o["f" + (i + 1) + ".body"] = item[1]; });
    d.rel.forEach(function (item) { o[item[0] + ".title"] = item[1]; o[item[0] + ".body"] = item[2]; });
    d.up.forEach(function (item) { o[item[0] + ".title"] = item[1]; o[item[0] + ".body"] = item[2]; });
    var keys = ["faq.install.title","faq.install.p1","faq.install.p2","faq.install.before.title","faq.install.before.1","faq.install.before.2","faq.install.before.3","faq.install.update.title","faq.install.update.body","faq.passkeys.title","faq.passkeys.p1","faq.passkeys.p2","faq.passkeys.why.title","faq.passkeys.why.1","faq.passkeys.why.2","faq.passkeys.why.3","faq.passkeys.why.4","faq.passkeys.requirements.title","faq.passkeys.req.windows","faq.passkeys.req.macos","faq.passkeys.req.ios","faq.passkeys.req.android","faq.passkeys.recovery"];
    keys.forEach(function (key, i) { o[key] = d.faq[i]; });
    Object.assign(T[lang], o);
  }
  ["ko", "uk", "pl"].forEach(function (lang) { applyLocalized(lang, sets[lang]); });
})();

(function () {
  var sets = {
    el: {
      core: [["Σάρωση και εμπλουτισμός","Σαρώστε barcodes και πάρτε αυτόματα metadata και εξώφυλλα."],["Φίλτρα και αναζήτηση","Αναζητήστε ανά τίτλο, είδος ή σκηνοθέτη και φιλτράρετε ανά format."],["Backup και restore","Δημιουργήστε πλήρη backups και επαναφέρετέ τα όταν χρειάζεται."],["Λειτουργεί offline","Χρησιμοποιήστε το DiscVault ως PWA χωρίς internet."],["Ασφαλές από σχεδιασμό","Self-hosted λειτουργία με έλεγχο της διαδρομής δεδομένων."],["Έτοιμο για MCP","Συνδέστε AI assistants μέσω του MCP endpoint του DiscVault."]],
      rel: [["f7","Διαχείριση χρηστών","Προσθέστε χρήστες με λογαριασμούς και δικαιώματα."],["f8","Ομάδες","Οργανώστε τη συλλογή σε ομάδες και κοινές λίστες."],["f9","Ανάκτηση passkey","Ανακτήστε λογαριασμό χωρίς passwords ή emails."],["f10","MemberGroups","Οι χρήστες δημιουργούν ομάδες και προσκαλούν άλλους."],["f11","Watchlist και ιστορικό","Παρακολουθήστε τίτλους και προσωπική watchlist."],["f12","Πολλές γλώσσες","Το DiscVault υποστηρίζει πολλές γλώσσες interface."],["f13","Εγγραφή μόνο με πρόσκληση","Οι νέοι χρήστες χρειάζονται πρόσκληση admin."],["f14","Push notifications","Ειδοποιήστε μέλη ομάδας για νέους δίσκους."],["f15","MCP ανά χρήστη","Προσωπικό API key συνδέει μόνο τα δικά σας δεδομένα."],["f16","Εμπλουτισμός metadata MovieVault","Εμπλουτίστε releases και box sets με community metadata."],["s_edition","Παρακολούθηση edition και version","Καταγράψτε Steelbook, Director's Cut, Limited Edition και άλλα."],["s_plex","Σύγκριση με Plex / Jellyfin","Συγκρίνετε φυσικούς δίσκους με ψηφιακούς τίτλους."]],
      up: [["s1","Εισαγωγή υπάρχουσας συλλογής","Εισαγάγετε CSV ή Letterboxd export."],["s2","Στατιστικά συλλογής","Δείτε formats, είδη, runtime και ανάπτυξη."],["s3","Προσαρμοσμένα tags και λίστες","Δημιουργήστε ελεύθερα tags και προσωπικές λίστες."],["s4","Tracker δανεισμού","Παρακολουθήστε σε ποιον δανείσατε δίσκους."],["s5","Webhooks και notifications","Στείλτε events για προσθήκες, backups και καθυστερημένους δανεισμούς."],["s6","Wishlist","Παρακολουθήστε δίσκους που θέλετε να προσθέσετε."],["s_region","Ένδειξη region","Καταγράψτε regions Blu-ray και DVD."],["s_picker","Τι να δω απόψε","Το DiscVault επιλέγει από το ράφι σας με φίλτρα."],["s_rating","Προσωπική αξιολόγηση","Προσθέστε δική σας βαθμολογία ξεχωριστά από IMDb."],["s_sort","Ταξινόμηση IMDb / TMDb","Ταξινομήστε τη συλλογή με βάση ratings."],["s_public","Δημόσια σελίδα προφίλ","Μοιραστείτε read-only link συλλογής."],["s_watchreq","Αιτήματα watchlist","Φίλοι ζητούν τίτλους σε shared group."],["s_letterboxd","Συγχρονισμός Letterboxd","Συγχρονίστε ιστορικό και ratings."]],
      faq: ["Εγκατάσταση και ενημερώσεις","Το DiscVault τρέχει self-hosted. Χρησιμοποιήστε all-in-one Docker container ή Unraid template με persistent storage.","Για stable χρήση επιλέξτε latest, για πρώιμες λειτουργίες DiscVault 26 επιλέξτε beta.","Πριν την ενημέρωση","Δημιουργήστε backup ή αντιγράψτε persistent data με σταματημένο container.","Ελέγξτε release notes μεταξύ beta και stable.","Μην αλλάζετε environment variables ή volume mappings χωρίς οδηγία.","Ενημέρωση","Κάντε pull το νέο image, ξαναδημιουργήστε το container με τις ίδιες ρυθμίσεις και ελέγξτε collection, users, passkeys, backups και settings.","Passkeys έναντι passwords","Το DiscVault χρησιμοποιεί passkeys ώστε να μη χρειάζεται password.","Τα passkeys είναι ασφαλέστερα επειδή αποθηκεύεται public key, όχι reusable password.","Γιατί είναι ασφαλέστερα και ευκολότερα","Ανθεκτικά σε phishing: δουλεύουν μόνο στο σωστό site.","Χωρίς shared secret: αποθηκεύεται public key.","Μοναδικά ανά υπηρεσία: το DiscVault passkey δεν επαναχρησιμοποιείται αλλού.","Γρήγορη σύνδεση: ξεκλειδώστε τη συσκευή αντί για password.","Ελάχιστες απαιτήσεις","Windows: browser με passkey support και Windows Hello.","macOS: Ventura 13+ με iCloud Keychain ή passkey manager.","iPhone/iPad: iOS/iPadOS 16+ με iCloud Keychain και 2FA.","Android: Android 9+ με passkey provider και screen lock.","Συμβουλή: καταχωρίστε περισσότερα από ένα passkey."]
    },
    hu: {
      core: [["Szkennelés és gazdagítás","Olvasson be vonalkódokat, majd töltse le automatikusan a metaadatokat és borítókat."],["Szűrés és keresés","Keressen cím, műfaj vagy rendező szerint, és szűrjön formátumra."],["Backup és restore","Készítsen teljes backupot, és állítsa vissza, amikor szükséges."],["Offline működik","Használja a DiscVaultot PWA-ként internet nélkül."],["Biztonságos kialakítás","Self-hosted futtatás saját adatútvonallal."],["MCP-ready","Csatlakoztasson AI assistantokat a DiscVault MCP endpointján keresztül."]],
      rel: [["f7","Felhasználókezelés","Adjon hozzá felhasználókat fiókokkal és jogosultságokkal."],["f8","Csoportok","Rendezze a gyűjteményt csoportokba és közös listákba."],["f9","Passkey helyreállítás","Állítson helyre fiókot passwords vagy emails nélkül."],["f10","MemberGroups","A felhasználók csoportokat hozhatnak létre és meghívhatnak másokat."],["f11","Watchlist és előzmények","Kövesse a nézett címeket és személyes watchlistet."],["f12","Több nyelv","A DiscVault több felületnyelvet támogat."],["f13","Meghívásos regisztráció","Új felhasználókhoz admin meghívó szükséges."],["f14","Push értesítések","Értesítse a csoporttagokat új lemezekről."],["f15","Felhasználói MCP-hozzáférés","Személyes API key csak saját adatokat kapcsol össze."],["f16","MovieVault metaadat-gazdagítás","Gazdagítsa a kiadásokat és box seteket community metaadatokkal."],["s_edition","Kiadás- és verziókövetés","Rögzítse a Steelbook, Director's Cut, Limited Edition és más kiadásokat."],["s_plex","Összehasonlítás Plex / Jellyfin rendszerrel","Hasonlítsa össze a fizikai lemezeket digitális címekkel."]],
      up: [["s1","Meglévő gyűjtemény importálása","Importáljon CSV-t vagy Letterboxd exportot."],["s2","Gyűjteménystatisztika","Tekintse meg a formátumokat, műfajokat, runtime-ot és növekedést."],["s3","Egyedi tagek és listák","Hozzon létre szabad tageket és személyes listákat."],["s4","Kölcsönzéskövető","Kövesse, kinek adott kölcsön lemezeket."],["s5","Webhooks és értesítések","Küldjön eseményeket hozzáadásról, backupról és lejárt kölcsönről."],["s6","Wishlist","Kövesse a hozzáadni kívánt lemezeket."],["s_region","Region jelző","Rögzítse a Blu-ray és DVD régiókat."],["s_picker","Mit nézzünk ma este","A DiscVault szűrők alapján választ a polcról."],["s_rating","Személyes értékelés","Adjon saját értékelést az IMDb-től függetlenül."],["s_sort","IMDb / TMDb szerinti rendezés","Rendezze a gyűjteményt értékelés alapján."],["s_public","Nyilvános profiloldal","Osszon meg read-only gyűjteménylinket."],["s_watchreq","Watchlist kérések","Barátok címeket kérhetnek shared groupban."],["s_letterboxd","Letterboxd sync","Szinkronizálja a nézési előzményeket és értékeléseket."]],
      faq: ["Telepítés és frissítések","A DiscVault self-hosted módon fut. Használja az all-in-one Docker containert vagy Unraid templatet persistent storage mellett.","Stable használathoz latest, korai DiscVault 26 funkciókhoz beta tag ajánlott.","Frissítés előtt","Készítsen backupot vagy másolja a persistent data mappát leállított container mellett.","Ellenőrizze a release notes-t beta és stable között.","Ne módosítsa az environment variables vagy volume mappings értékeket útmutatás nélkül.","Frissítés","Pullolja az új image-et, hozza létre újra a containert azonos beállításokkal, majd ellenőrizze a collection, users, passkeys, backups és settings részeket.","Passkeys kontra passwords","A DiscVault passkeys-t használ, így nem kell passwordöt létrehozni, megjegyezni vagy megosztani.","A passkeys biztonságosabb, mert public key tárolódik, nem reusable password.","Miért biztonságosabb és egyszerűbb","Phishingálló: csak a megfelelő site-on működik.","Nincs shared secret: public key tárolódik.","Szolgáltatásonként egyedi: DiscVault passkey nem használható máshol.","Gyors belépés: eszközfeloldás password helyett.","Minimum követelmények","Windows: passkey supportos browser és Windows Hello.","macOS: Ventura 13+ iCloud Keychainnel vagy passkey managerrel.","iPhone/iPad: iOS/iPadOS 16+ iCloud Keychainnel és 2FA-val.","Android: Android 9+ passkey providerrel és screen lockkal.","Tipp: regisztráljon több passkeyt."]
    }
  };
  function applyLocalized(lang, d) {
    var o = {};
    d.core.forEach(function (item, i) { o["f" + (i + 1) + ".title"] = item[0]; o["f" + (i + 1) + ".body"] = item[1]; });
    d.rel.forEach(function (item) { o[item[0] + ".title"] = item[1]; o[item[0] + ".body"] = item[2]; });
    d.up.forEach(function (item) { o[item[0] + ".title"] = item[1]; o[item[0] + ".body"] = item[2]; });
    var keys = ["faq.install.title","faq.install.p1","faq.install.p2","faq.install.before.title","faq.install.before.1","faq.install.before.2","faq.install.before.3","faq.install.update.title","faq.install.update.body","faq.passkeys.title","faq.passkeys.p1","faq.passkeys.p2","faq.passkeys.why.title","faq.passkeys.why.1","faq.passkeys.why.2","faq.passkeys.why.3","faq.passkeys.why.4","faq.passkeys.requirements.title","faq.passkeys.req.windows","faq.passkeys.req.macos","faq.passkeys.req.ios","faq.passkeys.req.android","faq.passkeys.recovery"];
    keys.forEach(function (key, i) { o[key] = d.faq[i]; });
    Object.assign(T[lang], o);
  }
  ["el", "hu"].forEach(function (lang) { applyLocalized(lang, sets[lang]); });
})();

(function () {
  var sets = {
    cs: {
      core: [["Skenování a obohacení","Skenujte čárové kódy kamerou a automaticky načítejte metadata a obaly."],["Filtrování a hledání","Hledejte podle názvu, žánru nebo režiséra a filtrujte podle formátu."],["Backup a restore","Vytvářejte úplné zálohy a obnovujte je podle potřeby."],["Funguje offline","Používejte DiscVault jako PWA i bez internetu."],["Bezpečné od návrhu","Self-hosted provoz vám dává kontrolu nad datovou cestou."],["MCP ready","Připojte AI assistants přes MCP endpoint DiscVault."]],
      rel: [["f7","Správa uživatelů","Přidejte uživatele s účty a oprávněními."],["f8","Skupiny","Organizujte kolekci do skupin a sdílených seznamů."],["f9","Obnova passkey","Obnovte účet bez passwords nebo emailů."],["f10","MemberGroups","Uživatelé mohou vytvářet skupiny a zvát ostatní."],["f11","Watchlist a historie","Sledujte zhlédnuté tituly a osobní watchlist."],["f12","Více jazyků","DiscVault podporuje více jazyků rozhraní."],["f13","Registrace pouze na pozvánku","Noví uživatelé potřebují pozvánku admina."],["f14","Push oznámení","Upozorněte členy skupiny na nové disky."],["f15","MCP přístup v rozsahu uživatele","Osobní API key propojí jen vlastní data."],["f16","Obohacení metadat MovieVault","Obohaťte vydání a box sety komunitními metadaty."],["s_edition","Sledování edicí a verzí","Zaznamenejte Steelbook, Director's Cut, Limited Edition a další edice."],["s_plex","Porovnání s Plex / Jellyfin","Porovnejte fyzické disky s digitálními tituly."]],
      up: [["s1","Import stávající kolekce","Importujte CSV nebo Letterboxd export."],["s2","Statistiky kolekce","Zobrazte formáty, žánry, runtime a růst."],["s3","Vlastní tagy a seznamy","Vytvářejte volné tagy a osobní seznamy."],["s4","Sledování půjček","Sledujte, komu jste půjčili disky."],["s5","Webhooks a oznámení","Odesílejte události pro přidání, backup a opožděné půjčky."],["s6","Wishlist","Sledujte disky, které chcete přidat."],["s_region","Ukazatel regionu","Zaznamenávejte regiony Blu-ray a DVD."],["s_picker","Co sledovat dnes večer","DiscVault vybere z vaší police podle filtrů."],["s_rating","Osobní hodnocení","Přidejte vlastní hodnocení nezávisle na IMDb."],["s_sort","Řazení podle IMDb / TMDb","Řaďte kolekci podle hodnocení."],["s_public","Veřejná stránka profilu","Sdílejte read-only odkaz na kolekci."],["s_watchreq","Požadavky watchlistu","Přátelé mohou požádat o titul ve sdílené skupině."],["s_letterboxd","Letterboxd sync","Synchronizujte historii sledování a hodnocení."]],
      faq: ["Instalace a aktualizace","DiscVault běží self-hosted. Použijte all-in-one Docker container nebo Unraid template s persistent storage.","Pro stabilní použití zvolte latest, pro rané funkce DiscVault 26 zvolte beta.","Před aktualizací","Vytvořte backup nebo zkopírujte persistent data, když je container zastavený.","Při přechodu mezi beta a stable zkontrolujte release notes.","Neměňte environment variables ani volume mappings bez pokynů.","Aktualizace","Stáhněte nový image, znovu vytvořte container se stejným nastavením a ověřte collection, users, passkeys, backups a settings.","Passkeys versus passwords","DiscVault používá passkeys, takže nemusíte vytvářet, pamatovat si ani sdílet password.","Passkeys jsou bezpečnější, protože DiscVault ukládá public key, ne reusable password.","Proč jsou bezpečnější a jednodušší","Odolné proti phishingu: fungují jen na správném webu.","Žádné shared secret: ukládá se public key.","Unikátní pro službu: DiscVault passkey nelze použít jinde.","Rychlé přihlášení: odemkněte zařízení místo psaní password.","Minimální požadavky","Windows: browser s passkey support a Windows Hello.","macOS: Ventura 13+ s iCloud Keychain nebo passkey managerem.","iPhone/iPad: iOS/iPadOS 16+ s iCloud Keychain a 2FA.","Android: Android 9+ s passkey providerem a screen lock.","Tip: zaregistrujte více než jeden passkey."]
    },
    tr: {
      core: [["Tara ve zenginleştir","Kamera ile barkod tarayın, meta verileri ve kapak görsellerini otomatik alın."],["Filtrele ve ara","Başlık, tür veya yönetmene göre arayın ve formata göre filtreleyin."],["Backup ve restore","Tam yedekler oluşturun ve gerektiğinde geri yükleyin."],["Offline çalışır","DiscVault'u PWA olarak kullanın ve internet olmadan yönetin."],["Güvenli tasarım","Self-hosted çalışma ile veri yolunuzu kontrol edin."],["MCP ready","AI assistants'ı DiscVault MCP endpoint üzerinden bağlayın."]],
      rel: [["f7","Kullanıcı yönetimi","Hesap ve izinlerle birden çok kullanıcı ekleyin."],["f8","Gruplar","Koleksiyonu gruplara ve paylaşılan listelere ayırın."],["f9","Passkey kurtarma","Hesabı passwords veya emails olmadan kurtarın."],["f10","MemberGroups","Kullanıcılar grup oluşturup başkalarını davet edebilir."],["f11","Watchlist ve geçmiş","İzlenen başlıkları ve kişisel watchlist'i takip edin."],["f12","Birden çok dil","DiscVault birden çok arayüz dilini destekler."],["f13","Yalnızca davetli kayıt","Yeni kullanıcılar admin daveti gerektirir."],["f14","Push bildirimleri","Yeni disk eklendiğinde grup üyelerini bilgilendirin."],["f15","Kullanıcı kapsamlı MCP erişimi","Kişisel API key yalnızca kendi verilerinizi bağlar."],["f16","MovieVault metadata zenginleştirme","Sürümleri ve box setleri community metadata ile zenginleştirin."],["s_edition","Edition ve version takibi","Steelbook, Director's Cut, Limited Edition ve diğer sürümleri kaydedin."],["s_plex","Plex / Jellyfin ile karşılaştır","Fiziksel diskleri dijital başlıklarla karşılaştırın."]],
      up: [["s1","Mevcut koleksiyonu içe aktar","CSV veya Letterboxd export içe aktarın."],["s2","Koleksiyon istatistikleri","Formatları, türleri, runtime ve büyümeyi görün."],["s3","Özel tags ve listeler","Serbest tags ve kişisel listeler oluşturun."],["s4","Ödünç takipçisi","Diskleri kime verdiğinizi takip edin."],["s5","Webhooks ve bildirimler","Ekleme, backup ve gecikmiş ödünç olayları gönderin."],["s6","Wishlist","Eklemek istediğiniz diskleri takip edin."],["s_region","Region göstergesi","Blu-ray ve DVD regions kaydedin."],["s_picker","Bu akşam ne izlenir","DiscVault filtrelerle rafınızdan seçer."],["s_rating","Kişisel puan","IMDb'den bağımsız kendi puanınızı ekleyin."],["s_sort","IMDb / TMDb puanına göre sırala","Koleksiyonu puana göre sıralayın."],["s_public","Herkese açık profil sayfası","Read-only koleksiyon bağlantısı paylaşın."],["s_watchreq","Watchlist istekleri","Arkadaşlar shared group içinde başlık isteyebilir."],["s_letterboxd","Letterboxd sync","İzleme geçmişi ve puanları senkronize edin."]],
      faq: ["Kurulum ve güncellemeler","DiscVault self-hosted çalışır. all-in-one Docker container veya persistent storage ile Unraid template kullanın.","Stable kullanım için latest, erken DiscVault 26 özellikleri için beta seçin.","Güncellemeden önce","Backup oluşturun veya container dururken persistent data kopyalayın.","Beta ve stable arasında geçerken release notes kontrol edin.","Talimat yoksa environment variables ve volume mappings değiştirmeyin.","Güncelleme","Yeni image'i pull edin, aynı settings ile container'ı yeniden oluşturun ve collection, users, passkeys, backups ve settings kontrol edin.","Passkeys ve passwords","DiscVault passkeys kullanır; password oluşturmanız, hatırlamanız veya paylaşmanız gerekmez.","Passkeys daha güvenlidir çünkü DiscVault reusable password değil public key saklar.","Neden daha güvenli ve kolay","Phishing'e dayanıklı: yalnızca doğru sitede çalışır.","Shared secret yok: public key saklanır.","Servise özel: DiscVault passkey başka yerde kullanılamaz.","Hızlı giriş: password yazmak yerine cihaz kilidini açın.","Minimum gereksinimler","Windows: passkey support olan browser ve Windows Hello.","macOS: Ventura 13+ ve iCloud Keychain veya passkey manager.","iPhone/iPad: iOS/iPadOS 16+ ile iCloud Keychain ve 2FA.","Android: Android 9+ ile passkey provider ve screen lock.","İpucu: birden fazla passkey kaydedin."]
    }
  };
  function applyLocalized(lang, d) {
    var o = {};
    d.core.forEach(function (item, i) { o["f" + (i + 1) + ".title"] = item[0]; o["f" + (i + 1) + ".body"] = item[1]; });
    d.rel.forEach(function (item) { o[item[0] + ".title"] = item[1]; o[item[0] + ".body"] = item[2]; });
    d.up.forEach(function (item) { o[item[0] + ".title"] = item[1]; o[item[0] + ".body"] = item[2]; });
    var keys = ["faq.install.title","faq.install.p1","faq.install.p2","faq.install.before.title","faq.install.before.1","faq.install.before.2","faq.install.before.3","faq.install.update.title","faq.install.update.body","faq.passkeys.title","faq.passkeys.p1","faq.passkeys.p2","faq.passkeys.why.title","faq.passkeys.why.1","faq.passkeys.why.2","faq.passkeys.why.3","faq.passkeys.why.4","faq.passkeys.requirements.title","faq.passkeys.req.windows","faq.passkeys.req.macos","faq.passkeys.req.ios","faq.passkeys.req.android","faq.passkeys.recovery"];
    keys.forEach(function (key, i) { o[key] = d.faq[i]; });
    Object.assign(T[lang], o);
  }
  ["cs", "tr"].forEach(function (lang) { applyLocalized(lang, sets[lang]); });
})();

(function () {
  var legacy = {
    en: [
      "Staying on the old version",
      "If you are not ready to move to DiscVault 26 yet, you can stay on the previous DiscVault generation by using the <code>legacy</code> Docker image tag.",
      "Use <code>ghcr.io/helmerzNL/DiscVault:legacy</code> instead of <code>latest</code> or <code>beta</code> in your Docker, Docker Compose, or Unraid template configuration. Keep your existing volume mappings, environment variables, ports, and persistent data path unchanged.",
      "Recommended steps",
      "Create a backup before changing the image tag.",
      "Change only the image tag to <code>legacy</code>.",
      "Pull the image, recreate the container, and confirm that your collection opens normally.",
      "The legacy image is meant for people who want to keep running the old version while DiscVault 26 beta matures. New DiscVault 26 features, migration improvements, and future interface updates are added to the beta and later production images instead."
    ],
    nl: [
      "Op de oude versie blijven",
      "Ben je nog niet klaar om naar DiscVault 26 te gaan, dan kun je op de vorige generatie DiscVault blijven door de Docker image-tag <code>legacy</code> te gebruiken.",
      "Gebruik <code>ghcr.io/helmerzNL/DiscVault:legacy</code> in plaats van <code>latest</code> of <code>beta</code> in je Docker-, Docker Compose- of Unraid-templateconfiguratie. Laat je bestaande volume mappings, environment variables, poorten en persistente datamap ongewijzigd.",
      "Aanbevolen stappen",
      "Maak een back-up voordat je de image-tag wijzigt.",
      "Wijzig alleen de image-tag naar <code>legacy</code>.",
      "Pull de image, maak de container opnieuw aan en controleer of je collectie normaal opent.",
      "De legacy image is bedoeld voor mensen die de oude versie willen blijven draaien terwijl DiscVault 26 beta verder rijpt. Nieuwe DiscVault 26-features, migratieverbeteringen en toekomstige interface-updates worden toegevoegd aan de beta en later aan de productie-images."
    ],
    fr: [
      "Rester sur l'ancienne version",
      "Si vous n'etes pas encore pret a passer a DiscVault 26, vous pouvez rester sur la generation precedente de DiscVault avec le tag d'image Docker <code>legacy</code>.",
      "Utilisez <code>ghcr.io/helmerzNL/DiscVault:legacy</code> au lieu de <code>latest</code> ou <code>beta</code> dans Docker, Docker Compose ou le template Unraid. Conservez vos volumes, variables d'environnement, ports et chemin de donnees persistantes.",
      "Etapes recommandees",
      "Creez une sauvegarde avant de modifier le tag de l'image.",
      "Ne changez que le tag de l'image vers <code>legacy</code>.",
      "Telechargez l'image, recreez le conteneur et verifiez que votre collection s'ouvre normalement.",
      "L'image legacy est destinee aux personnes qui veulent continuer a utiliser l'ancienne version pendant que DiscVault 26 beta murit. Les nouvelles fonctions DiscVault 26, les ameliorations de migration et les futures mises a jour d'interface sont ajoutees aux images beta puis production."
    ],
    de: [
      "Auf der alten Version bleiben",
      "Wenn du noch nicht zu DiscVault 26 wechseln mochtest, kannst du mit dem Docker-Image-Tag <code>legacy</code> auf der vorherigen DiscVault-Generation bleiben.",
      "Verwende <code>ghcr.io/helmerzNL/DiscVault:legacy</code> statt <code>latest</code> oder <code>beta</code> in Docker, Docker Compose oder deiner Unraid-Template-Konfiguration. Behalte bestehende Volume-Mappings, Umgebungsvariablen, Ports und den persistenten Datenpfad bei.",
      "Empfohlene Schritte",
      "Erstelle ein Backup, bevor du den Image-Tag anderst.",
      "Andere nur den Image-Tag auf <code>legacy</code>.",
      "Pull das Image, erstelle den Container neu und prufe, ob deine Sammlung normal geoffnet wird.",
      "Das legacy Image ist fur Nutzer gedacht, die die alte Version weiter betreiben mochten, wahrend DiscVault 26 beta reift. Neue DiscVault 26-Funktionen, Migrationsverbesserungen und kunftige Interface-Updates landen stattdessen in den beta- und spateren production-Images."
    ],
    es: [
      "Permanecer en la version anterior",
      "Si todavia no quieres pasar a DiscVault 26, puedes seguir usando la generacion anterior de DiscVault con la etiqueta de imagen Docker <code>legacy</code>.",
      "Usa <code>ghcr.io/helmerzNL/DiscVault:legacy</code> en lugar de <code>latest</code> o <code>beta</code> en Docker, Docker Compose o la plantilla de Unraid. Mantén sin cambios los volumenes, variables de entorno, puertos y ruta de datos persistentes.",
      "Pasos recomendados",
      "Crea una copia de seguridad antes de cambiar la etiqueta de la imagen.",
      "Cambia solo la etiqueta de la imagen a <code>legacy</code>.",
      "Descarga la imagen, recrea el contenedor y confirma que tu coleccion se abre con normalidad.",
      "La imagen legacy esta pensada para quienes quieren seguir ejecutando la version anterior mientras DiscVault 26 beta madura. Las nuevas funciones de DiscVault 26, las mejoras de migracion y las futuras actualizaciones de interfaz se agregan a las imagenes beta y luego de produccion."
    ],
    pt: [
      "Ficar na versao antiga",
      "Se ainda nao estiveres pronto para mudar para o DiscVault 26, podes ficar na geracao anterior do DiscVault usando a tag de imagem Docker <code>legacy</code>.",
      "Usa <code>ghcr.io/helmerzNL/DiscVault:legacy</code> em vez de <code>latest</code> ou <code>beta</code> na configuracao Docker, Docker Compose ou template Unraid. Mantem os volumes, variaveis de ambiente, portas e caminho de dados persistentes inalterados.",
      "Passos recomendados",
      "Cria uma copia de seguranca antes de mudar a tag da imagem.",
      "Altera apenas a tag da imagem para <code>legacy</code>.",
      "Faz pull da imagem, recria o container e confirma que a colecao abre normalmente.",
      "A imagem legacy destina-se a quem quer continuar a usar a versao antiga enquanto o DiscVault 26 beta amadurece. As novas funcionalidades do DiscVault 26, melhorias de migracao e futuras atualizacoes de interface entram nas imagens beta e depois production."
    ],
    it: [
      "Restare sulla vecchia versione",
      "Se non sei ancora pronto a passare a DiscVault 26, puoi restare sulla generazione precedente di DiscVault usando il tag immagine Docker <code>legacy</code>.",
      "Usa <code>ghcr.io/helmerzNL/DiscVault:legacy</code> invece di <code>latest</code> o <code>beta</code> nella configurazione Docker, Docker Compose o nel template Unraid. Mantieni invariati volumi, variabili d'ambiente, porte e percorso dei dati persistenti.",
      "Passaggi consigliati",
      "Crea un backup prima di modificare il tag dell'immagine.",
      "Cambia solo il tag dell'immagine in <code>legacy</code>.",
      "Esegui il pull dell'immagine, ricrea il container e verifica che la collezione si apra normalmente.",
      "L'immagine legacy e pensata per chi vuole continuare a usare la vecchia versione mentre DiscVault 26 beta matura. Le nuove funzioni di DiscVault 26, i miglioramenti di migrazione e i futuri aggiornamenti dell'interfaccia vengono aggiunti alle immagini beta e poi production."
    ],
    sv: [
      "Stanna pa den gamla versionen",
      "Om du inte ar redo att ga over till DiscVault 26 kan du stanna pa den tidigare DiscVault-generationen genom att anvanda Docker image-taggen <code>legacy</code>.",
      "Anvand <code>ghcr.io/helmerzNL/DiscVault:legacy</code> i stallet for <code>latest</code> eller <code>beta</code> i Docker, Docker Compose eller Unraid-mallen. Behall befintliga volym-mappningar, miljo-variabler, portar och persistent datasokvag oforandrade.",
      "Rekommenderade steg",
      "Skapa en backup innan du andrar image-taggen.",
      "Andra endast image-taggen till <code>legacy</code>.",
      "Gor pull av imagen, skapa om containern och kontrollera att samlingen oppnas normalt.",
      "Legacy-imagen ar avsedd for dem som vill kora den gamla versionen medan DiscVault 26 beta mognar. Nya DiscVault 26-funktioner, migreringsforbattringar och kommande interface-uppdateringar laggs i beta- och senare production-images."
    ],
    no: [
      "Bli pa den gamle versjonen",
      "Hvis du ikke er klar til a ga til DiscVault 26 enna, kan du bli pa forrige DiscVault-generasjon ved a bruke Docker image-taggen <code>legacy</code>.",
      "Bruk <code>ghcr.io/helmerzNL/DiscVault:legacy</code> i stedet for <code>latest</code> eller <code>beta</code> i Docker, Docker Compose eller Unraid-template. Behold eksisterende volumkoblinger, miljo-variabler, porter og persistent datasti uendret.",
      "Anbefalte steg",
      "Lag en sikkerhetskopi for du endrer image-taggen.",
      "Endre bare image-taggen til <code>legacy</code>.",
      "Pull imaget, opprett containeren pa nytt og bekreft at samlingen apnes normalt.",
      "Legacy-imaget er ment for deg som vil fortsette med den gamle versjonen mens DiscVault 26 beta modnes. Nye DiscVault 26-funksjoner, migreringsforbedringer og fremtidige grensesnittoppdateringer legges til beta- og senere produksjonsimages."
    ],
    fi: [
      "Pysy vanhassa versiossa",
      "Jos et ole viela valmis siirtymaan DiscVault 26:een, voit pysya edellisessa DiscVault-sukupolvessa kayttamalla Docker-imagen tagia <code>legacy</code>.",
      "Kayta <code>ghcr.io/helmerzNL/DiscVault:legacy</code> tagien <code>latest</code> tai <code>beta</code> sijaan Docker-, Docker Compose- tai Unraid-template-asetuksissa. Pida nykyiset volume mappingit, ymparistomuuttujat, portit ja pysyvan datan polku ennallaan.",
      "Suositellut vaiheet",
      "Tee varmuuskopio ennen image-tagin vaihtamista.",
      "Vaihda vain image-tagiksi <code>legacy</code>.",
      "Pullaa image, luo container uudelleen ja varmista, etta kokoelma avautuu normaalisti.",
      "Legacy-image on tarkoitettu kayttajille, jotka haluavat jatkaa vanhalla versiolla DiscVault 26 betan kypsyessa. Uudet DiscVault 26 -ominaisuudet, migraatioparannukset ja tulevat kayttoliittymapaivitykset lisataan beta- ja myohemmin production-imageihin."
    ],
    da: [
      "Bliv pa den gamle version",
      "Hvis du endnu ikke er klar til at skifte til DiscVault 26, kan du blive pa den tidligere DiscVault-generation ved at bruge Docker image-tagget <code>legacy</code>.",
      "Brug <code>ghcr.io/helmerzNL/DiscVault:legacy</code> i stedet for <code>latest</code> eller <code>beta</code> i Docker, Docker Compose eller Unraid-template. Behold dine eksisterende volume mappings, miljo-variabler, porte og persistente datasti uandret.",
      "Anbefalede trin",
      "Lav en backup, for du andrer image-tagget.",
      "Andr kun image-tagget til <code>legacy</code>.",
      "Pull imaget, genskab containeren og bekraft, at din samling abner normalt.",
      "Legacy-imaget er til dem, der vil fortsatte med den gamle version, mens DiscVault 26 beta modnes. Nye DiscVault 26-funktioner, migreringsforbedringer og kommende interface-opdateringer tilfojes beta- og senere production-images."
    ],
    ja: [
      "旧バージョンを使い続ける",
      "まだ DiscVault 26 へ移行する準備ができていない場合は、Docker image tag の <code>legacy</code> を使って以前の DiscVault 世代を使い続けられます。",
      "Docker、Docker Compose、Unraid template の設定で <code>latest</code> や <code>beta</code> の代わりに <code>ghcr.io/helmerzNL/DiscVault:legacy</code> を指定してください。既存の volume mappings、environment variables、ports、persistent data path は変更しないでください。",
      "推奨手順",
      "image tag を変更する前に backup を作成します。",
      "変更するのは image tag を <code>legacy</code> にする部分だけです。",
      "image を pull し、container を再作成して、collection が通常どおり開くことを確認します。",
      "legacy image は、DiscVault 26 beta が成熟するまで旧バージョンを動かし続けたい人向けです。新しい DiscVault 26 features、migration improvements、今後の interface updates は beta image と、その後の production image に追加されます。"
    ],
    zh: [
      "继续使用旧版本",
      "如果你还没有准备好迁移到 DiscVault 26，可以使用 Docker image tag <code>legacy</code> 继续运行上一代 DiscVault。",
      "在 Docker、Docker Compose 或 Unraid template 配置中，使用 <code>ghcr.io/helmerzNL/DiscVault:legacy</code> 代替 <code>latest</code> 或 <code>beta</code>。保持现有 volume mappings、environment variables、ports 和 persistent data path 不变。",
      "推荐步骤",
      "更改 image tag 前先创建 backup。",
      "只把 image tag 改为 <code>legacy</code>。",
      "pull 该 image，重新创建 container，并确认 collection 可以正常打开。",
      "legacy image 面向希望在 DiscVault 26 beta 成熟前继续运行旧版本的用户。新的 DiscVault 26 features、migration improvements 和未来 interface updates 会加入 beta image，并随后进入 production image。"
    ],
    ko: [
      "이전 버전에 머무르기",
      "아직 DiscVault 26으로 이동할 준비가 되지 않았다면 Docker image tag <code>legacy</code>를 사용해 이전 DiscVault 세대를 계속 사용할 수 있습니다.",
      "Docker, Docker Compose 또는 Unraid template 설정에서 <code>latest</code>나 <code>beta</code> 대신 <code>ghcr.io/helmerzNL/DiscVault:legacy</code>를 사용하세요. 기존 volume mappings, environment variables, ports, persistent data path는 그대로 유지하세요.",
      "권장 단계",
      "image tag를 변경하기 전에 backup을 만드세요.",
      "image tag만 <code>legacy</code>로 변경하세요.",
      "image를 pull하고 container를 다시 만든 뒤 collection이 정상적으로 열리는지 확인하세요.",
      "legacy image는 DiscVault 26 beta가 성숙하는 동안 이전 버전을 계속 실행하려는 사용자를 위한 것입니다. 새로운 DiscVault 26 features, migration improvements, future interface updates는 beta image와 이후 production image에 추가됩니다."
    ],
    uk: [
      "Залишитися на старій версії",
      "Якщо ви ще не готові переходити на DiscVault 26, можна залишитися на попередньому поколінні DiscVault за допомогою Docker image tag <code>legacy</code>.",
      "У Docker, Docker Compose або Unraid template використовуйте <code>ghcr.io/helmerzNL/DiscVault:legacy</code> замість <code>latest</code> чи <code>beta</code>. Не змінюйте наявні volume mappings, environment variables, ports і persistent data path.",
      "Рекомендовані кроки",
      "Створіть backup перед зміною image tag.",
      "Змініть лише image tag на <code>legacy</code>.",
      "Зробіть pull image, створіть container заново й перевірте, що collection відкривається нормально.",
      "Legacy image призначений для тих, хто хоче продовжувати використовувати стару версію, поки DiscVault 26 beta дозріває. Нові DiscVault 26 features, migration improvements і майбутні interface updates додаються до beta, а потім до production images."
    ],
    pl: [
      "Pozostanie przy starej wersji",
      "Jeśli nie chcesz jeszcze przechodzić na DiscVault 26, możesz zostać przy poprzedniej generacji DiscVault, używając Docker image tag <code>legacy</code>.",
      "W Docker, Docker Compose albo Unraid template użyj <code>ghcr.io/helmerzNL/DiscVault:legacy</code> zamiast <code>latest</code> lub <code>beta</code>. Pozostaw bez zmian dotychczasowe volume mappings, environment variables, ports i persistent data path.",
      "Zalecane kroki",
      "Utwórz backup przed zmianą image tag.",
      "Zmień tylko image tag na <code>legacy</code>.",
      "Wykonaj pull image, odtwórz container i sprawdź, czy collection otwiera się normalnie.",
      "Legacy image jest dla osób, które chcą nadal uruchamiać starą wersję, gdy DiscVault 26 beta dojrzewa. Nowe DiscVault 26 features, migration improvements i przyszłe interface updates trafiają do beta, a później do production images."
    ],
    el: [
      "Παραμονή στην παλιά έκδοση",
      "Αν δεν είστε ακόμη έτοιμοι να μεταβείτε στο DiscVault 26, μπορείτε να μείνετε στην προηγούμενη γενιά DiscVault χρησιμοποιώντας το Docker image tag <code>legacy</code>.",
      "Σε Docker, Docker Compose ή Unraid template χρησιμοποιήστε <code>ghcr.io/helmerzNL/DiscVault:legacy</code> αντί για <code>latest</code> ή <code>beta</code>. Κρατήστε ίδια τα volume mappings, environment variables, ports και persistent data path.",
      "Προτεινόμενα βήματα",
      "Δημιουργήστε backup πριν αλλάξετε το image tag.",
      "Αλλάξτε μόνο το image tag σε <code>legacy</code>.",
      "Κάντε pull το image, ξαναδημιουργήστε το container και επιβεβαιώστε ότι το collection ανοίγει κανονικά.",
      "Το legacy image προορίζεται για όσους θέλουν να συνεχίσουν την παλιά έκδοση όσο ωριμάζει το DiscVault 26 beta. Τα νέα DiscVault 26 features, migration improvements και μελλοντικά interface updates προστίθενται στα beta και αργότερα production images."
    ],
    hu: [
      "Maradás a régi verzión",
      "Ha még nem áll készen a DiscVault 26-ra váltásra, a Docker image tag <code>legacy</code> használatával maradhat az előző DiscVault-generáción.",
      "Docker, Docker Compose vagy Unraid template konfigurációban használja a <code>ghcr.io/helmerzNL/DiscVault:legacy</code> image-et a <code>latest</code> vagy <code>beta</code> helyett. Hagyja változatlanul a volume mappings, environment variables, ports és persistent data path beállításokat.",
      "Javasolt lépések",
      "Készítsen backupot az image tag módosítása előtt.",
      "Csak az image taget módosítsa erre: <code>legacy</code>.",
      "Pullolja az image-et, hozza létre újra a containert, és ellenőrizze, hogy a collection normálisan megnyílik.",
      "A legacy image azoknak szól, akik a DiscVault 26 beta éréséig a régi verziót szeretnék futtatni. Az új DiscVault 26 features, migration improvements és jövőbeli interface updates a beta, majd a production images részei lesznek."
    ],
    cs: [
      "Zůstat na staré verzi",
      "Pokud ještě nejste připraveni přejít na DiscVault 26, můžete zůstat na předchozí generaci DiscVault pomocí Docker image tagu <code>legacy</code>.",
      "V Dockeru, Docker Compose nebo Unraid template použijte <code>ghcr.io/helmerzNL/DiscVault:legacy</code> místo <code>latest</code> nebo <code>beta</code>. Ponechte beze změny stávající volume mappings, environment variables, ports a persistent data path.",
      "Doporučené kroky",
      "Před změnou image tagu vytvořte backup.",
      "Změňte pouze image tag na <code>legacy</code>.",
      "Stáhněte image, znovu vytvořte container a ověřte, že se collection otevírá normálně.",
      "Legacy image je určen pro ty, kteří chtějí dál používat starou verzi, zatímco DiscVault 26 beta dozrává. Nové DiscVault 26 features, migration improvements a budoucí interface updates se přidávají do beta a později production images."
    ],
    tr: [
      "Eski sürümde kalmak",
      "DiscVault 26'ya geçmeye henüz hazır değilseniz, Docker image tag <code>legacy</code> kullanarak önceki DiscVault neslinde kalabilirsiniz.",
      "Docker, Docker Compose veya Unraid template yapılandırmasında <code>latest</code> ya da <code>beta</code> yerine <code>ghcr.io/helmerzNL/DiscVault:legacy</code> kullanın. Mevcut volume mappings, environment variables, ports ve persistent data path ayarlarını değiştirmeyin.",
      "Önerilen adımlar",
      "Image tag değiştirmeden önce backup oluşturun.",
      "Yalnızca image tag değerini <code>legacy</code> yapın.",
      "Image'i pull edin, container'ı yeniden oluşturun ve collection'ın normal açıldığını doğrulayın.",
      "Legacy image, DiscVault 26 beta olgunlaşırken eski sürümü çalıştırmaya devam etmek isteyenler içindir. Yeni DiscVault 26 features, migration improvements ve future interface updates beta image'e, ardından production images'a eklenir."
    ]
  };
  var keys = [
    "faq.legacy.title",
    "faq.legacy.p1",
    "faq.legacy.p2",
    "faq.legacy.steps.title",
    "faq.legacy.steps.1",
    "faq.legacy.steps.2",
    "faq.legacy.steps.3",
    "faq.legacy.note"
  ];
  Object.keys(legacy).forEach(function (lang) {
    if (!T[lang]) return;
    keys.forEach(function (key, index) {
      T[lang][key] = legacy[lang][index];
    });
  });
})();

(function () {
  var keys = [
    "hero.eyebrow",
    "hero.lead",
    "hero.btn.get",
    "hero.point3",
    "cap.v26.library",
    "v26.eyebrow",
    "v26.badge.beta",
    "v26.f3.body",
    "screenshots.label.v26",
    "install.h2",
    "install.beta.badge",
    "install.beta.title",
    "install.beta.body",
    "install.stable.title",
    "install.stable.body",
    "install.note.body",
    "faq.install.p2"
  ];
  var updates = {
    en: ["DiscVault 26 is available now","DiscVault 26 can be installed and used today. It brings a new desktop-first library, richer detail pages, PostgreSQL foundations, plugins, migration tools, and better admin controls for collectors who want to keep their physical media catalog self-hosted.","Install v26","PostgreSQL-backed v26 stack","DiscVault 26 library","DiscVault 26","Available now","DiscVault 26 uses a PostgreSQL-backed model and includes a guided path to inspect and import existing DiscVault data while keeping media files in place.","DiscVault 26","Install DiscVault 26, or stay on the Legacy release.","DiscVault 26","v26 image","Install DiscVault 26 with the v26 image. This is the current DiscVault 26 release.","DiscVault Legacy","Stay on the old Legacy version with the legacy image:","Create a backup first, keep your persistent data and volume mappings, and use the migration guide before moving an existing library to DiscVault 26.","To install DiscVault 26, use the <code>v26</code> image tag. To stay on the old Legacy version, use the <code>legacy</code> tag instead of <code>latest</code>."],
    nl: ["DiscVault 26 is nu beschikbaar","DiscVault 26 kan nu geinstalleerd en gebruikt worden. Het brengt een nieuwe desktop-first bibliotheek, rijkere detailpagina's, PostgreSQL-fundamenten, plugins, migratietools en betere admin-controls voor verzamelaars die hun fysieke media self-hosted willen catalogiseren.","Installeer v26","PostgreSQL-backed v26 stack","DiscVault 26 bibliotheek","DiscVault 26","Nu beschikbaar","DiscVault 26 gebruikt een PostgreSQL-backed model en bevat een begeleid pad om bestaande DiscVault-data te controleren en te importeren terwijl mediabestanden op hun plek blijven.","DiscVault 26","Installeer DiscVault 26, of blijf op de Legacy-release.","DiscVault 26","v26 image","Installeer DiscVault 26 met de v26 image. Dit is de huidige DiscVault 26 release.","DiscVault Legacy","Blijf op de oude Legacy-versie met de legacy image:","Maak eerst een back-up, behoud je persistente data en volume mappings, en gebruik de migratiegids voordat je een bestaande bibliotheek naar DiscVault 26 verplaatst.","Gebruik de image-tag <code>v26</code> om DiscVault 26 te installeren. Gebruik de tag <code>legacy</code> in plaats van <code>latest</code> om op de oude Legacy-versie te blijven."],
    de: ["DiscVault 26 ist jetzt verfügbar","DiscVault 26 kann jetzt installiert und genutzt werden. Es bringt eine neue Desktop-Bibliothek, reichere Detailseiten, PostgreSQL-Grundlage, Plugins, Migrationstools und bessere Admin-Werkzeuge für selbst gehostete physische Mediensammlungen.","v26 installieren","PostgreSQL-basierter v26 Stack","DiscVault 26 Bibliothek","DiscVault 26","Jetzt verfügbar","DiscVault 26 nutzt ein PostgreSQL-basiertes Modell und bietet einen geführten Weg, bestehende DiscVault-Daten zu prüfen und zu importieren, während Mediendateien an Ort und Stelle bleiben.","DiscVault 26","Installiere DiscVault 26 oder bleibe beim Legacy-Release.","DiscVault 26","v26 Image","Installiere DiscVault 26 mit dem v26 Image. Das ist das aktuelle DiscVault 26 Release.","DiscVault Legacy","Bleibe mit dem legacy Image auf der alten Legacy-Version:","Erstelle zuerst ein Backup, behalte persistente Daten und Volume-Mappings bei und nutze den Migrationsleitfaden, bevor du eine bestehende Bibliothek nach DiscVault 26 verschiebst.","Nutze den Image-Tag <code>v26</code>, um DiscVault 26 zu installieren. Nutze <code>legacy</code> statt <code>latest</code>, um auf der alten Legacy-Version zu bleiben."],
    fr: ["DiscVault 26 est disponible","DiscVault 26 peut etre installe et utilise aujourd'hui. Il apporte une bibliotheque desktop-first, des pages detail enrichies, une base PostgreSQL, des plugins, des outils de migration et de meilleurs controles admin pour cataloguer des medias physiques en self-hosted.","Installer v26","Stack v26 base sur PostgreSQL","Bibliotheque DiscVault 26","DiscVault 26","Disponible","DiscVault 26 utilise un modele base sur PostgreSQL et inclut un chemin guide pour verifier et importer les donnees DiscVault existantes tout en gardant les fichiers media en place.","DiscVault 26","Installez DiscVault 26 ou restez sur la version Legacy.","DiscVault 26","Image v26","Installez DiscVault 26 avec l'image v26. C'est la version DiscVault 26 actuelle.","DiscVault Legacy","Restez sur l'ancienne version Legacy avec l'image legacy :","Creez d'abord une sauvegarde, gardez les donnees persistantes et les volumes, puis utilisez le guide de migration avant de passer une bibliotheque existante a DiscVault 26.","Pour installer DiscVault 26, utilisez le tag <code>v26</code>. Pour rester sur l'ancienne version Legacy, utilisez <code>legacy</code> au lieu de <code>latest</code>."],
    es: ["DiscVault 26 ya está disponible","DiscVault 26 ya se puede instalar y usar. Trae una biblioteca desktop-first, paginas de detalle mas completas, base PostgreSQL, plugins, herramientas de migracion y mejores controles admin para catalogos self-hosted de medios fisicos.","Instalar v26","Stack v26 con PostgreSQL","Biblioteca DiscVault 26","DiscVault 26","Disponible ahora","DiscVault 26 usa un modelo basado en PostgreSQL e incluye una ruta guiada para revisar e importar datos existentes de DiscVault manteniendo los archivos multimedia en su sitio.","DiscVault 26","Instala DiscVault 26 o permanece en Legacy.","DiscVault 26","Imagen v26","Instala DiscVault 26 con la imagen v26. Es la version actual de DiscVault 26.","DiscVault Legacy","Permanece en la version Legacy antigua con la imagen legacy:","Crea primero una copia, conserva datos persistentes y volumenes, y usa la guia de migracion antes de mover una biblioteca existente a DiscVault 26.","Para instalar DiscVault 26, usa el tag <code>v26</code>. Para quedarte en Legacy, usa <code>legacy</code> en lugar de <code>latest</code>."],
    pt: ["DiscVault 26 ja esta disponivel","O DiscVault 26 ja pode ser instalado e usado. Traz uma biblioteca desktop-first, paginas de detalhe mais ricas, base PostgreSQL, plugins, ferramentas de migracao e melhores controlos admin para colecoes fisicas self-hosted.","Instalar v26","Stack v26 com PostgreSQL","Biblioteca DiscVault 26","DiscVault 26","Disponivel agora","O DiscVault 26 usa um modelo baseado em PostgreSQL e inclui um caminho guiado para verificar e importar dados DiscVault existentes mantendo os ficheiros media no sitio.","DiscVault 26","Instale o DiscVault 26 ou fique na versao Legacy.","DiscVault 26","Imagem v26","Instale o DiscVault 26 com a imagem v26. Esta e a release atual do DiscVault 26.","DiscVault Legacy","Fique na antiga versao Legacy com a imagem legacy:","Crie primeiro um backup, mantenha dados persistentes e volume mappings, e use o guia de migracao antes de mover uma biblioteca existente para DiscVault 26.","Para instalar o DiscVault 26, use a tag <code>v26</code>. Para ficar na Legacy, use <code>legacy</code> em vez de <code>latest</code>."],
    it: ["DiscVault 26 e disponibile","DiscVault 26 puo essere installato e usato oggi. Porta una libreria desktop-first, pagine dettaglio piu ricche, base PostgreSQL, plugin, strumenti di migrazione e controlli admin migliori per cataloghi self-hosted.","Installa v26","Stack v26 con PostgreSQL","Libreria DiscVault 26","DiscVault 26","Disponibile ora","DiscVault 26 usa un modello basato su PostgreSQL e include un percorso guidato per verificare e importare dati DiscVault esistenti mantenendo i file media al loro posto.","DiscVault 26","Installa DiscVault 26 o resta sulla release Legacy.","DiscVault 26","Immagine v26","Installa DiscVault 26 con l'immagine v26. Questa e la release DiscVault 26 attuale.","DiscVault Legacy","Resta sulla vecchia versione Legacy con l'immagine legacy:","Crea prima un backup, mantieni dati persistenti e volume mappings, e usa la guida di migrazione prima di spostare una libreria esistente a DiscVault 26.","Per installare DiscVault 26 usa il tag <code>v26</code>. Per restare su Legacy usa <code>legacy</code> invece di <code>latest</code>."],
    sv: ["DiscVault 26 är tillgänglig nu","DiscVault 26 kan installeras och användas nu. Den ger en ny desktop-first-biblioteksvy, rikare detaljsidor, PostgreSQL-grund, plugins, migreringsverktyg och bättre admin-kontroller för self-hosted fysiska mediesamlingar.","Installera v26","PostgreSQL-baserad v26 stack","DiscVault 26 bibliotek","DiscVault 26","Tillgänglig nu","DiscVault 26 använder en PostgreSQL-baserad modell och har en guidad väg för att kontrollera och importera befintliga DiscVault-data medan mediafiler ligger kvar.","DiscVault 26","Installera DiscVault 26 eller stanna på Legacy.","DiscVault 26","v26 image","Installera DiscVault 26 med v26-imagen. Detta är aktuell DiscVault 26 release.","DiscVault Legacy","Stanna på gamla Legacy-versionen med legacy-imagen:","Skapa backup först, behåll persistent data och volume mappings, och använd migreringsguiden innan du flyttar ett befintligt bibliotek till DiscVault 26.","För att installera DiscVault 26, använd image-taggen <code>v26</code>. För att stanna på Legacy, använd <code>legacy</code> istället för <code>latest</code>."],
    no: ["DiscVault 26 er tilgjengelig nå","DiscVault 26 kan installeres og brukes nå. Den gir et nytt desktop-first bibliotek, rikere detaljsider, PostgreSQL-grunnlag, plugins, migreringsverktøy og bedre adminkontroller for self-hosted fysiske mediesamlinger.","Installer v26","PostgreSQL-basert v26 stack","DiscVault 26 bibliotek","DiscVault 26","Tilgjengelig nå","DiscVault 26 bruker en PostgreSQL-basert modell og har en guidet vei for a sjekke og importere eksisterende DiscVault-data mens mediefiler blir liggende.","DiscVault 26","Installer DiscVault 26 eller bli på Legacy.","DiscVault 26","v26 image","Installer DiscVault 26 med v26-imaget. Dette er gjeldende DiscVault 26 release.","DiscVault Legacy","Bli på gammel Legacy-versjon med legacy-imaget:","Lag backup først, behold persistent data og volume mappings, og bruk migreringsguiden før du flytter et eksisterende bibliotek til DiscVault 26.","For å installere DiscVault 26, bruk image-taggen <code>v26</code>. For å bli på Legacy, bruk <code>legacy</code> i stedet for <code>latest</code>."],
    fi: ["DiscVault 26 on nyt saatavilla","DiscVault 26 voidaan asentaa ja ottaa kayttoon nyt. Se tuo uuden desktop-first-kirjaston, rikkaammat tietosivut, PostgreSQL-pohjan, pluginit, migraatiotyokalut ja paremmat admin-kontrollit self-hosted fyysisille kokoelmille.","Asenna v26","PostgreSQL-pohjainen v26 stack","DiscVault 26 kirjasto","DiscVault 26","Saatavilla nyt","DiscVault 26 käyttää PostgreSQL-pohjaista mallia ja sisältää ohjatun polun olemassa olevan DiscVault-datan tarkistamiseen ja importointiin mediafileiden pysyessa paikallaan.","DiscVault 26","Asenna DiscVault 26 tai pysy Legacyssa.","DiscVault 26","v26 image","Asenna DiscVault 26 v26-imagella. Tämä on nykyinen DiscVault 26 release.","DiscVault Legacy","Pysy vanhassa Legacy-versiossa legacy-imagella:","Tee ensin backup, säilytä persistent data ja volume mappings, ja käytä migraatio-opasta ennen nykyisen kirjaston siirtämistä DiscVault 26:een.","Asenna DiscVault 26 käyttämällä image-tagia <code>v26</code>. Pysy Legacyssa käyttämällä <code>legacy</code> tagia <code>latest</code> sijaan."],
    da: ["DiscVault 26 er tilgængelig nu","DiscVault 26 kan installeres og bruges nu. Den giver et nyt desktop-first bibliotek, rigere detaljesider, PostgreSQL-grundlag, plugins, migreringsværktøjer og bedre admin-kontroller til self-hosted fysiske mediesamlinger.","Installer v26","PostgreSQL-baseret v26 stack","DiscVault 26 bibliotek","DiscVault 26","Tilgængelig nu","DiscVault 26 bruger en PostgreSQL-baseret model og har en guidet vej til at kontrollere og importere eksisterende DiscVault-data, mens mediefiler bliver liggende.","DiscVault 26","Installer DiscVault 26, eller bliv på Legacy.","DiscVault 26","v26 image","Installer DiscVault 26 med v26-imaget. Dette er den aktuelle DiscVault 26 release.","DiscVault Legacy","Bliv på den gamle Legacy-version med legacy-imaget:","Lav backup først, behold persistent data og volume mappings, og brug migreringsguiden før du flytter et eksisterende bibliotek til DiscVault 26.","For at installere DiscVault 26 skal du bruge image-tagget <code>v26</code>. For at blive på Legacy skal du bruge <code>legacy</code> i stedet for <code>latest</code>."],
    ja: ["DiscVault 26 は利用可能です","DiscVault 26 は今すぐインストールして利用できます。新しい desktop-first library、より詳しい detail pages、PostgreSQL foundation、plugins、migration tools、そして self-hosted の物理メディア catalog 向け admin controls を備えています。","v26 をインストール","PostgreSQL-backed v26 stack","DiscVault 26 library","DiscVault 26","利用可能","DiscVault 26 は PostgreSQL-backed model を使い、既存の DiscVault data を確認して import する guided path を備え、media files はそのまま保持します。","DiscVault 26","DiscVault 26 をインストールするか Legacy release に残ります。","DiscVault 26","v26 image","DiscVault 26 は v26 image でインストールします。これが現在の DiscVault 26 release です。","DiscVault Legacy","古い Legacy version に残る場合は legacy image を使います:","まず backup を作り、persistent data と volume mappings を保持し、既存 library を DiscVault 26 に移す前に migration guide を使ってください。","DiscVault 26 をインストールするには image tag <code>v26</code> を使います。古い Legacy version に残るには <code>latest</code> ではなく <code>legacy</code> を使います。"],
    zh: ["DiscVault 26 现已可用","DiscVault 26 现在可以安装和使用。它带来新的 desktop-first library、更丰富的 detail pages、PostgreSQL foundation、plugins、migration tools，以及适合 self-hosted 实体媒体 catalog 的更好 admin controls。","安装 v26","PostgreSQL-backed v26 stack","DiscVault 26 library","DiscVault 26","现已可用","DiscVault 26 使用 PostgreSQL-backed model，并提供 guided path 来检查和 import 现有 DiscVault data，同时 media files 保持原位。","DiscVault 26","安装 DiscVault 26，或保留 Legacy release。","DiscVault 26","v26 image","使用 v26 image 安装 DiscVault 26。这是当前 DiscVault 26 release。","DiscVault Legacy","使用 legacy image 保留旧 Legacy version:","先创建 backup，保持 persistent data 和 volume mappings，并在将现有 library 移到 DiscVault 26 前使用 migration guide。","要安装 DiscVault 26，请使用 image tag <code>v26</code>。要保留旧 Legacy version，请使用 <code>legacy</code> 而不是 <code>latest</code>。"],
    ko: ["DiscVault 26 사용 가능","DiscVault 26은 지금 설치하고 사용할 수 있습니다. 새로운 desktop-first library, 더 풍부한 detail pages, PostgreSQL foundation, plugins, migration tools, self-hosted 물리 미디어 catalog를 위한 향상된 admin controls를 제공합니다.","v26 설치","PostgreSQL-backed v26 stack","DiscVault 26 library","DiscVault 26","사용 가능","DiscVault 26은 PostgreSQL-backed model을 사용하며 기존 DiscVault data를 확인하고 import하는 guided path를 제공하고 media files는 제자리에 유지합니다.","DiscVault 26","DiscVault 26을 설치하거나 Legacy release에 머무르세요.","DiscVault 26","v26 image","v26 image로 DiscVault 26을 설치하세요. 이것이 현재 DiscVault 26 release입니다.","DiscVault Legacy","legacy image로 이전 Legacy version을 유지하세요:","먼저 backup을 만들고 persistent data와 volume mappings를 유지하며 기존 library를 DiscVault 26으로 이동하기 전에 migration guide를 사용하세요.","DiscVault 26을 설치하려면 image tag <code>v26</code>를 사용하세요. 이전 Legacy version을 유지하려면 <code>latest</code> 대신 <code>legacy</code>를 사용하세요."],
    uk: ["DiscVault 26 доступний","DiscVault 26 вже можна встановити й використовувати. Він додає нову desktop-first library, багатші detail pages, PostgreSQL foundation, plugins, migration tools і кращі admin controls для self-hosted catalog фізичних медіа.","Встановити v26","PostgreSQL-backed v26 stack","DiscVault 26 library","DiscVault 26","Доступно","DiscVault 26 використовує PostgreSQL-backed model і має guided path для перевірки та import наявних DiscVault data, залишаючи media files на місці.","DiscVault 26","Встановіть DiscVault 26 або залишайтеся на Legacy release.","DiscVault 26","v26 image","Встановіть DiscVault 26 з v26 image. Це поточний DiscVault 26 release.","DiscVault Legacy","Залишайтеся на старій Legacy version з legacy image:","Спершу створіть backup, збережіть persistent data і volume mappings та використайте migration guide перед перенесенням наявної library до DiscVault 26.","Щоб встановити DiscVault 26, використовуйте image tag <code>v26</code>. Щоб залишитися на старій Legacy version, використовуйте <code>legacy</code> замість <code>latest</code>."],
    pl: ["DiscVault 26 jest dostępny","DiscVault 26 można już zainstalować i używać. Dodaje nową desktop-first library, bogatsze detail pages, PostgreSQL foundation, plugins, migration tools i lepsze admin controls dla self-hosted catalog fizycznych mediów.","Zainstaluj v26","PostgreSQL-backed v26 stack","DiscVault 26 library","DiscVault 26","Dostępne","DiscVault 26 używa PostgreSQL-backed model i ma guided path do sprawdzania oraz import DiscVault data, zostawiając media files na miejscu.","DiscVault 26","Zainstaluj DiscVault 26 albo zostań przy Legacy release.","DiscVault 26","v26 image","Zainstaluj DiscVault 26 z v26 image. To aktualny DiscVault 26 release.","DiscVault Legacy","Zostań przy starej Legacy version z legacy image:","Najpierw utwórz backup, zachowaj persistent data i volume mappings, a przed przeniesieniem istniejącej library do DiscVault 26 użyj migration guide.","Aby zainstalować DiscVault 26, użyj image tag <code>v26</code>. Aby zostać przy starej Legacy version, użyj <code>legacy</code> zamiast <code>latest</code>."],
    el: ["Το DiscVault 26 είναι διαθέσιμο","Το DiscVault 26 μπορεί πλέον να εγκατασταθεί και να χρησιμοποιηθεί. Φέρνει νέο desktop-first library, πλουσιότερα detail pages, PostgreSQL foundation, plugins, migration tools και καλύτερα admin controls για self-hosted catalog φυσικών media.","Εγκατάσταση v26","PostgreSQL-backed v26 stack","DiscVault 26 library","DiscVault 26","Διαθέσιμο","Το DiscVault 26 χρησιμοποιεί PostgreSQL-backed model και περιλαμβάνει guided path για έλεγχο και import υπάρχοντων DiscVault data, κρατώντας τα media files στη θέση τους.","DiscVault 26","Εγκαταστήστε DiscVault 26 ή μείνετε στο Legacy release.","DiscVault 26","v26 image","Εγκαταστήστε το DiscVault 26 με το v26 image. Αυτή είναι η τρέχουσα DiscVault 26 release.","DiscVault Legacy","Μείνετε στην παλιά Legacy version με το legacy image:","Δημιουργήστε πρώτα backup, κρατήστε persistent data και volume mappings και χρησιμοποιήστε το migration guide πριν μεταφέρετε υπάρχουσα library στο DiscVault 26.","Για εγκατάσταση DiscVault 26 χρησιμοποιήστε image tag <code>v26</code>. Για παραμονή στην παλιά Legacy version χρησιμοποιήστε <code>legacy</code> αντί για <code>latest</code>."],
    hu: ["A DiscVault 26 elérhető","A DiscVault 26 már telepíthető és használható. Új desktop-first library, gazdagabb detail pages, PostgreSQL foundation, plugins, migration tools és jobb admin controls érkezik self-hosted fizikai média cataloghoz.","v26 telepítése","PostgreSQL-backed v26 stack","DiscVault 26 library","DiscVault 26","Elérhető","A DiscVault 26 PostgreSQL-backed modelt használ, és guided path segít meglévő DiscVault data ellenőrzésében és importjában, miközben a media files helyben maradnak.","DiscVault 26","Telepítse a DiscVault 26-ot, vagy maradjon a Legacy release-en.","DiscVault 26","v26 image","Telepítse a DiscVault 26-ot a v26 image segítségével. Ez az aktuális DiscVault 26 release.","DiscVault Legacy","Maradjon a régi Legacy versionön a legacy image használatával:","Először készítsen backupot, tartsa meg persistent data és volume mappings beállításait, és használja a migration guide-ot, mielőtt meglévő library-t DiscVault 26-ra visz.","DiscVault 26 telepítéséhez használja a <code>v26</code> image taget. A régi Legacy version megtartásához <code>latest</code> helyett <code>legacy</code> taget használjon."],
    cs: ["DiscVault 26 je dostupný","DiscVault 26 lze nyní nainstalovat a používat. Přináší novou desktop-first library, bohatší detail pages, PostgreSQL foundation, plugins, migration tools a lepší admin controls pro self-hosted catalog fyzických médií.","Instalovat v26","PostgreSQL-backed v26 stack","DiscVault 26 library","DiscVault 26","Dostupné","DiscVault 26 používá PostgreSQL-backed model a obsahuje guided path pro kontrolu a import existujících DiscVault data, zatímco media files zůstávají na místě.","DiscVault 26","Nainstalujte DiscVault 26 nebo zůstaňte na Legacy release.","DiscVault 26","v26 image","Nainstalujte DiscVault 26 pomocí v26 image. Toto je aktuální DiscVault 26 release.","DiscVault Legacy","Zůstaňte na staré Legacy version s legacy image:","Nejprve vytvořte backup, ponechte persistent data a volume mappings a před přesunem existující library do DiscVault 26 použijte migration guide.","Pro instalaci DiscVault 26 použijte image tag <code>v26</code>. Pro setrvání na staré Legacy version použijte <code>legacy</code> místo <code>latest</code>."],
    tr: ["DiscVault 26 kullanıma hazır","DiscVault 26 artık kurulup kullanılabilir. Yeni desktop-first library, daha zengin detail pages, PostgreSQL foundation, plugins, migration tools ve self-hosted fiziksel medya catalog için daha iyi admin controls getirir.","v26 kur","PostgreSQL-backed v26 stack","DiscVault 26 library","DiscVault 26","Kullanıma hazır","DiscVault 26 PostgreSQL-backed model kullanır ve mevcut DiscVault data için media files yerinde kalırken kontrol ve import sağlayan guided path içerir.","DiscVault 26","DiscVault 26 kurun veya Legacy release üzerinde kalın.","DiscVault 26","v26 image","DiscVault 26'yı v26 image ile kurun. Bu mevcut DiscVault 26 release'dir.","DiscVault Legacy","Eski Legacy version'da legacy image ile kalın:","Önce backup oluşturun, persistent data ve volume mappings koruyun, mevcut library DiscVault 26'ya taşınmadan önce migration guide kullanın.","DiscVault 26 kurmak için image tag <code>v26</code> kullanın. Eski Legacy version'da kalmak için <code>latest</code> yerine <code>legacy</code> kullanın."]
  };
  Object.keys(updates).forEach(function (lang) {
    if (!T[lang]) return;
    keys.forEach(function (key, index) {
      T[lang][key] = updates[lang][index];
    });
  });
})();

(function () {
  var notes = {
    en: "The legacy image is meant for people who want to keep running the old version. New DiscVault 26 features, migration improvements, and future interface updates are added to the v26 image instead.",
    nl: "De legacy image is bedoeld voor mensen die de oude versie willen blijven draaien. Nieuwe DiscVault 26-features, migratieverbeteringen en toekomstige interface-updates worden toegevoegd aan de v26 image.",
    de: "Das legacy Image ist für Nutzer gedacht, die die alte Version weiter betreiben möchten. Neue DiscVault 26-Funktionen, Migrationsverbesserungen und künftige Interface-Updates werden stattdessen dem v26 Image hinzugefügt.",
    fr: "L'image legacy est destinée aux personnes qui veulent continuer à utiliser l'ancienne version. Les nouvelles fonctions DiscVault 26, les améliorations de migration et les futures mises à jour d'interface sont ajoutées à l'image v26.",
    es: "La imagen legacy es para quienes quieren seguir usando la versión antigua. Las nuevas funciones de DiscVault 26, las mejoras de migración y las futuras actualizaciones de interfaz se añaden a la imagen v26.",
    pt: "A imagem legacy é para quem quer continuar a usar a versão antiga. As novas funcionalidades DiscVault 26, melhorias de migração e futuras atualizações de interface são adicionadas à imagem v26.",
    it: "L'immagine legacy è pensata per chi vuole continuare a usare la vecchia versione. Le nuove funzioni di DiscVault 26, i miglioramenti di migrazione e i futuri aggiornamenti dell'interfaccia vengono aggiunti all'immagine v26.",
    sv: "Legacy-imagen är avsedd för dem som vill fortsätta köra den gamla versionen. Nya DiscVault 26-funktioner, migreringsförbättringar och kommande gränssnittsuppdateringar läggs till i v26-imagen.",
    no: "Legacy-imaget er ment for deg som vil fortsette med den gamle versjonen. Nye DiscVault 26-funksjoner, migreringsforbedringer og fremtidige grensesnittoppdateringer legges til v26-imaget.",
    fi: "Legacy-image on tarkoitettu käyttäjille, jotka haluavat jatkaa vanhalla versiolla. Uudet DiscVault 26 -ominaisuudet, migraatioparannukset ja tulevat käyttöliittymäpäivitykset lisätään v26-imageen.",
    da: "Legacy-imaget er til dem, der vil fortsætte med den gamle version. Nye DiscVault 26-funktioner, migreringsforbedringer og kommende interface-opdateringer tilføjes v26-imaget.",
    ja: "legacy image は旧バージョンを使い続けたい人向けです。新しい DiscVault 26 features、migration improvements、今後の interface updates は v26 image に追加されます。",
    zh: "legacy image 面向希望继续运行旧版本的用户。新的 DiscVault 26 features、migration improvements 和未来 interface updates 会加入 v26 image。",
    ko: "legacy image는 이전 버전을 계속 실행하려는 사용자를 위한 것입니다. 새로운 DiscVault 26 features, migration improvements, future interface updates는 v26 image에 추가됩니다.",
    uk: "Legacy image призначений для тих, хто хоче продовжувати стару версію. Нові DiscVault 26 features, migration improvements і майбутні interface updates додаються до v26 image.",
    pl: "Legacy image jest dla osób, które chcą nadal używać starej wersji. Nowe DiscVault 26 features, migration improvements i przyszłe interface updates trafiają do v26 image.",
    el: "Το legacy image προορίζεται για όσους θέλουν να συνεχίσουν την παλιά έκδοση. Τα νέα DiscVault 26 features, migration improvements και μελλοντικά interface updates προστίθενται στο v26 image.",
    hu: "A legacy image azoknak szól, akik a régi verziót szeretnék futtatni. Az új DiscVault 26 features, migration improvements és jövőbeli interface updates a v26 image-be kerülnek.",
    cs: "Legacy image je určen pro ty, kteří chtějí dál používat starou verzi. Nové DiscVault 26 features, migration improvements a budoucí interface updates se přidávají do v26 image.",
    tr: "Legacy image, eski sürümü çalıştırmaya devam etmek isteyenler içindir. Yeni DiscVault 26 features, migration improvements ve future interface updates v26 image'e eklenir."
  };
  Object.keys(notes).forEach(function (lang) {
    if (T[lang]) T[lang]["faq.legacy.note"] = notes[lang];
  });
})();
