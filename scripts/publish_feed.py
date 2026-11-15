#!/usr/bin/env python3
"""Publica um episodio no feed "Newsletters do Luiz" (GitHub Pages).

Desenho:
- Branch `gh-pages` do repositorio guarda SO o que o feed serve: feed.xml,
  cover.jpg, episodes.json e a pasta audio/ com os ultimos N episodios.
- A cada publicacao a branch e recriada como UM unico commit (orfao) e enviada
  com force-push. Assim o historico nunca acumula audio antigo e o repositorio
  nao estoura o limite de ~1 GB.

Uso (dentro de um clone do repositorio, com permissao de push):
  python3 publish_feed.py --repo DIR --audio Arquivo.m4a \
      --tema "Marketing & Growth" --data 2026-11-17 \
      --descricao-arquivo descricao.txt [--titulo "..."] [--max 60]
"""
import argparse, datetime, email.utils, html, json, os, shutil, subprocess, sys

OWNER = os.environ.get("FEED_OWNER", "luizpiovesana-mkt")
REPO = os.environ.get("FEED_REPO", "newsletters-do-luiz")
BASE = "https://%s.github.io/%s" % (OWNER, REPO)
PROGRAMA = "Newsletters do Luiz"
AUTOR = "Luiz Piovesana"
RESUMO = ("Curadoria em áudio das newsletters do Luiz: Marketing & Growth, "
          "IA & Tecnologia, Liderança & Carreira e o wrap-up mensal.")
MIME = {".m4a": "audio/x-m4a", ".mp3": "audio/mpeg"}
MESES = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho",
         "agosto", "setembro", "outubro", "novembro", "dezembro"]


def sh(*cmd, cwd=None):
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if r.returncode:
        sys.exit("ERRO em %s:\n%s" % (" ".join(cmd), r.stderr[-2000:]))
    return r.stdout.strip()


def duracao(path):
    s = float(sh("ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=nw=1:nk=1", path))
    s = int(round(s))
    return "%d:%02d:%02d" % (s // 3600, s % 3600 // 60, s % 60)


def data_br(d):
    return "%d de %s" % (d.day, MESES[d.month - 1])


def desc_html(t):
    """Descricao multilinha -> HTML, para a lista de tempos aparecer como lista
    no app (pedido do Luiz em 15/nov/2026: "mostrar no descritivo do episodio os
    pontos de conteudos nos tempos corretos; facilita navegacao").
    Linha em branco separa paragrafo; quebra simples vira <br/>. Vai em CDATA,
    por isso o ']]>' precisa ser neutralizado antes."""
    t = t.replace("]]>", "]]&gt;")
    blocos = []
    for par in t.split("\n\n"):
        linhas = [html.escape(l.strip()) for l in par.splitlines() if l.strip()]
        if linhas:
            blocos.append("<p>" + "<br/>".join(linhas) + "</p>")
    return "".join(blocos)


def item_xml(ep):
    url = "%s/audio/%s" % (BASE, ep["arquivo"])
    return """    <item>
      <title>{t}</title>
      <description><![CDATA[{dh}]]></description>
      <itunes:summary>{d}</itunes:summary>
      <enclosure url="{u}" length="{n}" type="{m}"/>
      <guid isPermaLink="false">{g}</guid>
      <pubDate>{p}</pubDate>
      <itunes:duration>{dur}</itunes:duration>
      <itunes:episodeType>full</itunes:episodeType>
      <itunes:explicit>false</itunes:explicit>
    </item>""".format(t=html.escape(ep["titulo"]), d=html.escape(ep["descricao"]),
                      dh=desc_html(ep["descricao"]),
                      u=html.escape(url), n=ep["bytes"], m=ep["mime"],
                      g=html.escape(ep["guid"]), p=ep["pubDate"], dur=ep["duracao"])


def feed_xml(eps):
    agora = email.utils.format_datetime(datetime.datetime.now(datetime.timezone.utc))
    itens = "\n".join(item_xml(e) for e in eps)
    return """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd" xmlns:atom="http://www.w3.org/2005/Atom">
  <channel>
    <title>{prog}</title>
    <link>{base}/</link>
    <atom:link href="{base}/feed.xml" rel="self" type="application/rss+xml"/>
    <language>pt-br</language>
    <description>{res}</description>
    <itunes:summary>{res}</itunes:summary>
    <itunes:author>{aut}</itunes:author>
    <itunes:image href="{base}/cover.jpg"/>
    <image><url>{base}/cover.jpg</url><title>{prog}</title><link>{base}/</link></image>
    <itunes:category text="Business"/>
    <itunes:explicit>false</itunes:explicit>
    <itunes:type>episodic</itunes:type>
    <itunes:block>Yes</itunes:block>
    <lastBuildDate>{agora}</lastBuildDate>
{itens}
  </channel>
</rss>
""".format(prog=html.escape(PROGRAMA), base=BASE, res=html.escape(RESUMO),
           aut=html.escape(AUTOR), agora=agora, itens=itens)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--audio", required=True)
    ap.add_argument("--tema", required=True)
    ap.add_argument("--data", required=True, help="AAAA-MM-DD")
    ap.add_argument("--titulo")
    ap.add_argument("--descricao", default="")
    ap.add_argument("--descricao-arquivo")
    ap.add_argument("--max", type=int, default=60)
    ap.add_argument("--cover", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "cover.jpg"))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--token-file", help="github_token.env; sem ele usa as credenciais do remote origin")
    a = ap.parse_args()

    repo = os.path.abspath(a.repo)
    ext = os.path.splitext(a.audio)[1].lower()
    if ext not in MIME:
        sys.exit("ERRO: formato %s nao suportado (use .m4a ou .mp3)" % ext)
    d = datetime.date.fromisoformat(a.data)
    desc = a.descricao
    if a.descricao_arquivo:
        desc = open(a.descricao_arquivo, encoding="utf-8").read().strip()

    # Estado atual da branch gh-pages (se existir)
    # refspec explicito: em clone single-branch/shallow, "fetch origin gh-pages" nao cria origin/gh-pages
    sh("git", "fetch", "--depth=1", "origin", "+refs/heads/gh-pages:refs/remotes/origin/gh-pages", cwd=repo) if subprocess.run(
        ["git", "ls-remote", "--exit-code", "--heads", "origin", "gh-pages"],
        cwd=repo, capture_output=True).returncode == 0 else None
    work = os.path.join(repo, ".feed_work")
    shutil.rmtree(work, ignore_errors=True)
    os.makedirs(os.path.join(work, "audio"))
    eps = []
    tem_pages = subprocess.run(["git", "rev-parse", "--verify", "origin/gh-pages"],
                               cwd=repo, capture_output=True).returncode == 0
    if tem_pages:
        try:
            eps = json.loads(sh("git", "show", "origin/gh-pages:episodes.json", cwd=repo))
        except SystemExit:
            eps = []

    import re, unicodedata
    slug = unicodedata.normalize("NFD", a.tema).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^A-Za-z0-9]+", "-", slug).strip("-")
    nome = "%s_%s%s" % (a.data, slug, ext)
    guid = "newsletters-do-luiz-%s-%s" % (a.data, nome.rsplit(".", 1)[0])
    eps = [e for e in eps if e["guid"] != guid]  # republicar o mesmo dia substitui
    eps.insert(0, {
        "guid": guid, "arquivo": nome, "mime": MIME[ext],
        "titulo": a.titulo or "%s: %s" % (a.tema, data_br(d)),
        "descricao": desc or "%s, episódio de %s." % (a.tema, data_br(d)),
        "bytes": os.path.getsize(a.audio), "duracao": duracao(a.audio),
        "pubDate": email.utils.format_datetime(datetime.datetime(
            d.year, d.month, d.day, 6, 0, tzinfo=datetime.timezone(datetime.timedelta(hours=-3)))),
        "data": a.data,
    })
    eps.sort(key=lambda e: (e["data"], e["guid"]), reverse=True)
    eps = eps[:a.max]

    # Monta a arvore: audio novo vem do arquivo; os demais, da branch atual
    shutil.copy(a.audio, os.path.join(work, "audio", nome))
    for e in eps:
        if e["arquivo"] == nome:
            continue
        destino = os.path.join(work, "audio", e["arquivo"])
        with open(destino, "wb") as f:
            r = subprocess.run(["git", "show", "origin/gh-pages:audio/" + e["arquivo"]],
                               cwd=repo, stdout=f)
        if r.returncode:
            sys.exit("ERRO: audio antigo %s nao encontrado na branch" % e["arquivo"])
    if os.path.exists(a.cover):
        shutil.copy(a.cover, os.path.join(work, "cover.jpg"))
    elif tem_pages:  # nas rodadas da nuvem a capa vem da propria branch
        with open(os.path.join(work, "cover.jpg"), "wb") as f:
            subprocess.run(["git", "show", "origin/gh-pages:cover.jpg"], cwd=repo, stdout=f, check=True)
    else:
        sys.exit("ERRO: capa nao encontrada em %s" % a.cover)
    with open(os.path.join(work, "episodes.json"), "w", encoding="utf-8") as f:
        json.dump(eps, f, ensure_ascii=False, indent=1)
    with open(os.path.join(work, "feed.xml"), "w", encoding="utf-8") as f:
        f.write(feed_xml(eps))
    open(os.path.join(work, ".nojekyll"), "w").close()
    with open(os.path.join(work, "index.html"), "w", encoding="utf-8") as f:
        f.write('<!doctype html><meta charset="utf-8"><title>%s</title>'
                '<p>Feed: <a href="feed.xml">%s/feed.xml</a></p>' % (PROGRAMA, BASE))

    print("Episodios no feed: %d | novo: %s (%s)" % (len(eps), eps[0]["titulo"] if eps[0]["arquivo"] == nome else nome, duracao(a.audio)))
    if a.dry_run:
        print("dry-run: arvore em", work)
        return

    # Commit orfao unico + force push
    g = ["git", "--git-dir", os.path.join(repo, ".git"), "--work-tree", work]
    idx = os.path.join(repo, ".git", "feed_index")
    env = dict(os.environ, GIT_INDEX_FILE=idx)
    for cmd in (["add", "-A", "."],):
        subprocess.run(g + cmd, cwd=work, env=env, check=True)
    tree = subprocess.run(g + ["write-tree"], cwd=work, env=env, check=True,
                          capture_output=True, text=True).stdout.strip()
    commit = subprocess.run(g + ["commit-tree", tree, "-m", "Feed: %s" % eps[0]["titulo"]],
                            cwd=work, env=env, check=True, capture_output=True, text=True).stdout.strip()
    destino = "origin"
    if a.token_file:
        # aceita "GITHUB_TOKEN=xxx" ou so o token na primeira linha
        tok = open(a.token_file, encoding="utf-8").read().strip().splitlines()[0]
        tok = tok.split("=", 1)[1].strip() if tok.startswith("GITHUB_TOKEN=") else tok.strip()
        destino = "https://x-access-token:%s@github.com/%s/%s.git" % (tok, OWNER, REPO)
    r = subprocess.run(["git", "push", "--force", destino, "%s:refs/heads/gh-pages" % commit],
                       cwd=repo, capture_output=True, text=True)
    if r.returncode:
        err = r.stderr.replace(tok, "***") if a.token_file else r.stderr
        sys.exit("ERRO no push:\n" + err[-1500:])
    os.remove(idx)
    shutil.rmtree(work, ignore_errors=True)
    print("Publicado: %s/feed.xml" % BASE)


if __name__ == "__main__":
    main()
