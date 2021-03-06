import re
from functools import wraps
from os import makedirs
from os.path import join, dirname, realpath
from html import escape

import flask_admin as admin
from flask import Flask, render_template, session, redirect, url_for, request, abort, make_response
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_login import LoginManager, login_required, login_user, current_user, logout_user

from config import SECRET_KEY
from tools.admin import UserView, PageView, CheckCookieAdminView
from tools.models import UsersConnector, WikiPagesConnector
from tools.rendering import audio_render, WikiPageRenderer
from tools.users import User

app = Flask(__name__)

AUDIO_RENDER_FOLDER = join(dirname(realpath(__file__)), "static/sound/")


app.config['SECRET_KEY'] = SECRET_KEY

app.config.from_envvar('DEV_SETTINGS', silent=True)

# limiter to temper with registration abuse
registration_limiter = Limiter(
    app,
    key_func = get_remote_address)

# flask-login
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"
login_manager.login_message = "Vous devez être connectés pour créer ou éditer des pages"

# Setting up flask-admin
admin = admin.Admin(app, name='Wikiloult Admin', index_view=CheckCookieAdminView())
admin.add_view(UserView(UsersConnector().users, 'Users'))
admin.add_view(PageView(WikiPagesConnector().pages, 'Pages'))


@login_manager.user_loader
def load_user(user_cookie):
    return User(user_cookie)


def autologin(function):
    @wraps(function)
    def with_login():
        """Decorator that tries to log in automatically is the cookie is already
        set by a past login, by setting the session"""
        cookie = request.cookies.get("id", None)
        if cookie is not None:
            login_user(User(cookie))

    return function


@app.errorhandler(404)
def page_not_found(e):
    return render_template('404.html'), 404

@app.errorhandler(429)
def ratelimit_handler(e):
    return render_template('error.html',
                           message="Trop de requêtes : Nombre limite d'inscription atteint"), 429


@app.route("/")
@autologin
def home():
    return render_template("homepage.html")


@app.route("/login", methods=['GET', 'POST'])
@autologin
def login():
    """User login page"""
    if request.method == "GET":
        return render_template("login.html")

    elif request.method == "POST":
        user_cookie = request.form["user"]
        user_cnctr = UsersConnector()

        if not user_cnctr.user_exists(user_cookie):
            return redirect(url_for('register'))
        else:
            message = "Connectèw."

        login_user(User(user_cookie))
        return render_template("login.html", message=message)
    

@app.route("/logout")
@login_required
@autologin
def logout():
    """Logout of the website : destroy session and delete the cookie"""
    logout_user()
    resp = make_response(render_template("homepage.html"))
    resp.set_cookie('id', '', expires=0)  # destroy the cookie by making it expire immediately
    return resp


@app.route("/register", methods=['GET','POST'])
@autologin
@registration_limiter.limit("1/day", error_message="Une inscription par jour.", exempt_when= lambda: request.method == 'GET')
def register():
    user_cnctr = UsersConnector()
    if request.method == 'GET':
        if user_cnctr.user_exists(request.cookies.get("id", None)):
            message= "Connectèw."
        else:
            message=None
        return render_template("register.html", base_template='base.html', message=message)
    
    elif request.method == 'POST':
        user_cookie = request.form["user"]
        if not user_cnctr.user_exists(user_cookie):
            user_cnctr.register_user(user_cookie)
            message = """Votre compte utilisateur a été créé.
            Un administrateur doit le valider pour que vous puissiez aussi éditer des pages."""        
        else:
            message = "Connectèw."
            login_user(User(user_cookie))
        return render_template("register.html", message=message, base_template='base.html')


@app.route("/page/<page_name>")
@autologin
def page(page_name):
    """Display a wiki page"""
    page_cnctr = WikiPagesConnector()
    page_data = page_cnctr.get_page_data(page_name)
    return render_template("wiki_page.html",
                           page_data=page_data,
                           page_name=page_name,
                           audio_filename=page_name + ".wav")

@app.route("/page/<page_name>/history")
@autologin
def page_history(page_name):
    """Display a page's edit history"""
    page_cnctr = WikiPagesConnector()
    page_history = page_cnctr.get_page_history(page_name)
    return render_template("page_history.html",
                           page_history=reversed(page_history),
                           page_name=page_name)


@app.route("/page/<page_name>/edit", methods=['GET', 'POST'])
@login_required
@autologin
def page_edit(page_name):
    """Page edition form"""
    user_cnctr = UsersConnector()
    if not user_cnctr.is_allowed(current_user.cookie):
        return render_template("error.html", message="Vous n'êtes pas encore autorisé à éditer des pages")

    page_cnctr = WikiPagesConnector()
    if not page_cnctr.page_exists(page_name):
        return render_template("error.html", message="Impossible d'éditer une page qui n'existe paw")

    if request.method == "GET":
        page_data = page_cnctr.get_page_data(page_name)
        return render_template("page_edit.html",
                               page_name=page_name,
                               page_content=page_data["markdown_content"],
                               page_title=page_data["title"])

    elif request.method == "POST":
        title = request.form["title"]
        markdown_content = request.form["content"]

        if request.form.get("preview", None) is not None:
            markdown_renderer = WikiPageRenderer()
            html_render = markdown_renderer.render(escape(markdown_content))
            return render_template("page_edit.html",
                                   page_name=page_name,
                                   page_content=markdown_content,
                                   page_title=title,
                                   preview=html_render)

        if not title.strip() or not markdown_content.strip():
            return render_template("page_edit.html",
                                   page_name=page_name,
                                   page_content=markdown_content,
                                   page_title=title,
                                   message="Le titre ni le contenu ne doivent être vides.")

        page_cnctr.edit_page(page_name, markdown_content, title, current_user.cookie)
        audio_render(title, join(AUDIO_RENDER_FOLDER, page_name + ".wav"))
        user_cnctr.add_modification(current_user.cookie, page_name)
        return redirect(url_for("page", page_name=page_name))

@app.route("/restore")
@login_required
@autologin
def page_restore():
    """Display a page's edit history"""
    if not current_user.is_admin:
        abort(403)
    page_name = request.args.get('page_name')
    edit_id = int(request.args.get('edit_id'))
    page_cnctr = WikiPagesConnector()
    page_data = page_cnctr.get_page_data(page_name)
    if page_data is None:
        abort(500)

    page_history = page_cnctr.get_page_history(page_name)
    try:
        selected_edit = page_history[edit_id]
    except IndexError:
        abort(500)
    else:
        return render_template("page_edit.html",
                               page_name=page_name,
                               page_content=selected_edit["markdown"],
                               page_title=selected_edit["title"])


@app.route("/page/create", methods=['GET', 'POST'])
@login_required
@autologin
def page_create():
    """Page creation form (almost the same as the page edition form"""
    user_cnctr = UsersConnector()
    if not user_cnctr.is_allowed(current_user.cookie):
        return render_template("error.html", message="Vous n'êtes pas encore autorisé à éditer des pages")

    page_cnctr = WikiPagesConnector()
    if request.method == "GET":
        return render_template("page_create.html",
                               page_name=request.args.get("page_name", None))

    elif request.method == "POST":
        page_name = request.form["name"]
        title = request.form["title"]
        markdown_content = request.form["content"]

        if request.form.get("preview", None) is not None:
            markdown_renderer = WikiPageRenderer()
            html_render = markdown_renderer.render(escape(markdown_content))
            return render_template("page_create.html",
                                   page_content=markdown_content,
                                   page_title=title,
                                   page_name=page_name,
                                   preview=html_render)

        error_message = None
        if not title.strip() or not markdown_content.strip() or not page_name.strip():
            error_message = "Le nom de page, titre ou le contenu ne doivent être vides."
        elif not re.match("^[a-zA-Z_]*$", page_name):
            error_message = "Le nom de la page ne doit contenir que des lettres et des tirets du bas."
        elif page_cnctr.get_page_data(page_name) is not None:
            error_message = "Une page portant ce nom existe déjà, changez le nom svp mr."

        if error_message is not None:
            return render_template("page_create.html",
                                   page_content=markdown_content,
                                   page_title=title,
                                   page_name=page_name,
                                   message=error_message)

        page_cnctr.create_page(page_name.lower(), markdown_content, title, current_user.cookie)
        try:
            makedirs(AUDIO_RENDER_FOLDER)
        except FileExistsError:
            pass
        audio_render(title, join(AUDIO_RENDER_FOLDER, page_name + ".wav"))
        return redirect(url_for("page", page_name=page_name))


@app.route("/user/<user_id>")
@autologin
def user_page(user_id):
    """User's personal page"""
    user_cnctr = UsersConnector()
    user_data = user_cnctr.get_user_data(user_id)
    if user_data is None:
        abort(404)
    user_data["modifications"].reverse()
    if len(user_data["modifications"]) > 15:
        user_data["modifications"] = user_data["modifications"][:15]
    return render_template("user_page.html", user_data=user_data, user=User(user_data["_id"]))


@app.route("/user/edit", methods=['GET', 'POST'])
@login_required
@autologin
def profile_edit():
    user_cnctr = UsersConnector()
    if request.method == "GET":
        user_data = user_cnctr.get_user_data(current_user.user_id)
        return render_template("user_profile_edit.html", profile_markdown=user_data["personal_text_markdown"])

    elif request.method == "POST":
        markdown_content = request.form["content"]

        if request.form.get("preview", None) is not None:
            markdown_renderer = WikiPageRenderer()
            html_render = markdown_renderer.render(escape(markdown_content))
            return render_template("user_profile_edit.html",
                                   profile_markdown=markdown_content,
                                   preview=html_render)

        error_message = None
        if not markdown_content.strip():
            error_message = "Le contenu ne peut pas être vide wesh"

        if error_message is not None:
            return render_template("user_profile_edit.html",
                                   profile_markdown=markdown_content,
                                   message=error_message)

        user_cnctr.update_user_text(current_user.cookie, markdown_content)
        return redirect(url_for("user_page", user_id=current_user.user_id))


@app.route("/search")
@autologin
def search_page():
    """Search for a wiki page"""
    search_query = request.args.get('query', '')
    page_cnctr = WikiPagesConnector()
    results = page_cnctr.search_pages(search_query)
    for result in results:
        result["raw_text"] = re.sub('<[^<]+?>', '', result["html_content"])
    return render_template("page_search.html", results_list=results)


@app.route("/random")
@autologin
def random_page():
    """Search for a wiki page"""
    page_cnctr = WikiPagesConnector()
    return redirect(url_for("page", page_name=page_cnctr.get_random_page()))


@app.route("/last_edits")
@autologin
def last_edits():
    """Display pages that where last edited"""
    page_cnctr = WikiPagesConnector()
    last_edited_pages = []
    last_editor, last_page = None, None
    for page in page_cnctr.get_last_edited(30):
        if page["history"]["editor_cookie"] != last_editor or page["_id"] != last_page:
            page["raw_text"] = re.sub('<[^<]+?>', '', page["html_content"])
            page["last_editor"] = User(page["history"]["editor_cookie"])
            last_edited_pages.append(page)
            last_editor = page["history"]["editor_cookie"]
            last_page = page["_id"]

    return render_template("last_edited.html", results_list=last_edited_pages)

@app.route("/all")
@autologin
def all_pages():
    page_cnctr = WikiPagesConnector()
    return render_template("all_pages.html", pages_per_first_letter=page_cnctr.get_all_pages_sorted())

#### routes for static pages


@app.route("/rules")
@autologin
def rules():
    """Wiki rules"""
    return render_template("rules.html")


def main():
    app.run()

if __name__ == "__main__":
    app.config['DEBUG'] = True
    main()
