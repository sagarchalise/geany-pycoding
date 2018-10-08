/*
 *      demoplugin.c - this file is part of Geany, a fast and lightweight IDE
 *
 *      Copyright 2007-2012 Enrico Tröger <enrico(dot)troeger(at)uvena(dot)de>
 *      Copyright 2007-2012 Nick Treleaven <nick(dot)treleaven(at)btinternet(dot)com>
 *
 *      This program is free software; you can redistribute it and/or modify
 *      it under the terms of the GNU General Public License as published by
 *      the Free Software Foundation; either version 2 of the License, or
 *      (at your option) any later version.
 *
 *      This program is distributed in the hope that it will be useful,
 *      but WITHOUT ANY WARRANTY; without even the implied warranty of
 *      MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 *      GNU General Public License for more details.
 *
 *      You should have received a copy of the GNU General Public License along
 *      with this program; if not, write to the Free Software Foundation, Inc.,
 *      51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.
 */

/**
 * Demo plugin - example of a basic plugin for Geany. Adds a menu item to the
 * Tools menu.
 *
 * Note: This is not installed by default, but (on *nix) you can build it as follows:
 * cd plugins
 * make demoplugin.so
 *
 * Then copy or symlink the plugins/demoplugin.so file to ~/.config/geany/plugins
 * - it will be loaded at next startup.
 */


#include "geanyplugin.h"	/* plugin API, always comes first */

#include <gio/gio.h>

#define OBJECT_PATH "/org/gtk/GDBus/GeanyPyCodingObject"
#define BUS_NAME "org.gtk.GDBus.GeanyPyCodingServer"
#define INTERFACE_NAME "org.gtk.GDBus.GeanyPyCodingInterface"
//#define GEANY_PYDBUS_BIN "pycoding.py"


enum {
  KB_FORMAT_PYCODE,
  KB_COUNT
};
GDBusConnection *jedi_connection;
static GtkWidget *main_menu_item = NULL;
gboolean dbus_running = FALSE;

gboolean check_doc(GeanyDocument *doc){
    if(!DOC_VALID(doc)){
	return FALSE;
    }
    if(doc->file_type->id != GEANY_FILETYPES_PYTHON){
	return FALSE;
    }
}

static void on_document_save(GObject *obj, GeanyDocument *doc, gpointer user_data)
{
    if(!check_doc(doc)){
	return;
    }
    msgwin_clear_tab(MSG_MESSAGE);
    msgwin_clear_tab(MSG_COMPILER);
    GeanyPlugin *plugin = user_data;
    jedi_connection = g_bus_get_sync(G_BUS_TYPE_SESSION, NULL, NULL);
    if(jedi_connection == NULL){
	msgwin_compiler_add_string(COLOR_DARK_RED, "Couldnot Connect.");
	return;
    }
    GVariant *reply;
    reply = g_dbus_connection_call_sync(jedi_connection,
                             BUS_NAME,
                             OBJECT_PATH,
                             INTERFACE_NAME,
                             "Format",
                             g_variant_new ("(si)",
                                            sci_get_contents(doc->editor->sci, -1),
					    MAX(plugin->geany_data->editor_prefs->long_line_column, plugin->geany_data->editor_prefs->line_break_column)),
                             NULL,
                             G_DBUS_CALL_FLAGS_NONE,
                             -1,
			     NULL,
			    NULL);
    if(reply == NULL){
	keybindings_send_command(GEANY_KEY_GROUP_BUILD, GEANY_KEYS_BUILD_LINK);
    }
    else{
	gchar *msg;
	g_variant_get(reply, "(&s)", &msg);
	gint pos = sci_get_current_position(doc->editor->sci);
	sci_set_text(doc->editor->sci, msg);
	sci_set_current_position(doc->editor->sci, pos, TRUE);
	keybindings_send_command(GEANY_KEY_GROUP_FILE, GEANY_KEYS_FILE_SAVE);
    }
    g_variant_unref(reply);
}
static void on_document_action(GObject *obj, GeanyDocument *doc, gpointer user_data)
{
    if(!check_doc(doc)){
	return;
    }
    keybindings_send_command(GEANY_KEY_GROUP_BUILD, GEANY_KEYS_BUILD_LINK); 
}
static void show_autocomplete(ScintillaObject *sci, gsize rootlen, const gchar *words, gsize len)
{
	/* hide autocompletion if only option is already typed */
	if (rootlen >= len ||
		(words[rootlen] == '?' && rootlen >= len - 2))
	{
		sci_send_command(sci, SCI_AUTOCCANCEL);
		return;
	}
	scintilla_send_message(sci, SCI_AUTOCSHOW, rootlen, (sptr_t) words);
}
static void complete_python(GeanyEditor *editor, int ch, const gchar *text, GeanyProject *project){
    gint line, col, pos, rootlen;
    gboolean import_check = FALSE;
    ScintillaObject *sci;
    gchar *word_at_pos, *buffer;
    g_return_if_fail(editor !=NULL);
    if (text == NULL){
        switch(ch){
                case '\r':
		case '\n':
		case '>':
		case '/':
		case '(':
		case ')':
		case '{':
		case '[':
		case '"':
		case '\'':
		case '}':
		case ':':
                        return;
        }
        
    }
    sci = editor->sci;
    pos = sci_get_current_position(sci);
    line = sci_get_current_line(sci)+1;
    word_at_pos = g_strchug(sci_get_line(sci, line-1));
    if(word_at_pos == NULL){
	    return;
    }
    if(g_str_has_prefix(word_at_pos, "import") || g_str_has_prefix(word_at_pos, "from")){
	    buffer = sci_get_line(sci, line-1);
	    line = 1;
	    import_check = TRUE;
    } 
    else{
	    buffer = sci_get_contents_range(sci, 0, pos);
	    
    }              
    g_free(word_at_pos);
    word_at_pos = editor_get_word_at_pos(editor, pos, GEANY_WORDCHARS".");
    if(word_at_pos == NULL){
	    return;
    }
    col = sci_get_col_from_position(sci, pos);
    rootlen = strlen(word_at_pos);
    if (strstr(word_at_pos, ".") != NULL){
	    g_free(word_at_pos);
	    word_at_pos = editor_get_word_at_pos(editor, pos, NULL);
	    if(word_at_pos == NULL){
		    rootlen = 0;
	    }
	    else{
		    rootlen = strlen(word_at_pos);
		    g_free(word_at_pos);
	    }
    }
    else if((!import_check && rootlen < 2) || rootlen == 0 ){
	    g_free(word_at_pos);
	    return;
    }
    msgwin_clear_tab(MSG_COMPILER);
    msgwin_clear_tab(MSG_MESSAGE);
    jedi_connection = g_bus_get_sync(G_BUS_TYPE_SESSION, NULL, NULL);
    if(jedi_connection == NULL){
	msgwin_msg_add_string(COLOR_RED, -1, editor->document, "PyCoding Completion Issue. No Connection");
	return;
    }
    GVariant *reply;
    reply = g_dbus_connection_call_sync (jedi_connection,
                             BUS_NAME,
                             OBJECT_PATH,
                             INTERFACE_NAME,
                             "Complete",
                             g_variant_new ("(ssss)",
                                            buffer,
                                            (editor->document->real_path==NULL)?editor->document->file_name:editor->document->real_path,
					    (project == NULL)?"":project->base_path,
					    (text==NULL)?"":text),
                             NULL,
                             G_DBUS_CALL_FLAGS_NONE,
                             -1,
                             NULL,
                             NULL);
    gchar *msg;
    g_variant_get(reply, "(&s)", &msg);
    gint len = g_utf8_strlen(msg, -1);
    if(text == NULL){
	show_autocomplete(editor->sci, rootlen, msg, len);
    }
    else{
	if(len > 6){
		msgwin_msg_add_string(COLOR_BLACK, line-1, editor->document, msg);
		msgwin_switch_tab(MSG_MESSAGE, FALSE);
	}
    }
    g_variant_unref(reply);
    g_free(buffer);
    //g_free(msg);
    //g_dbus_connection_close_sync(jedi_connection, NULL, NULL);
}
static gboolean on_editor_notify(GObject *object, GeanyEditor *editor,
								 SCNotification *nt, gpointer data)
{
	if(!check_doc(editor->document)){
	    return FALSE;
	}
	GeanyPlugin *plugin = data;
        gint lexer, pos, style;
	/* For detailed documentation about the SCNotification struct, please see
	 * http://www.scintilla.org/ScintillaDoc.html#Notifications. */
        pos = sci_get_current_position(editor->sci);
	if (G_UNLIKELY(pos < 2))
		return FALSE;
        lexer = sci_get_lexer(editor->sci);
	style = sci_get_style_at(editor->sci, pos - 2);

	/* don't autocomplete in comments and strings */
	if (!highlighting_is_code_style(lexer, style))
		return FALSE;
	switch (nt->nmhdr.code)
	{
		case SCN_CHARADDED:
                        complete_python(editor, nt->ch, NULL, plugin->geany_data->app->project);
                        break;
                case SCN_AUTOCSELECTION:
                        complete_python(editor, nt->ch, nt->text, plugin->geany_data->app->project);
                        break;
	}

	return FALSE;
}
static PluginCallback demo_callbacks[] =
{
	/* Set 'after' (third field) to TRUE to run the callback @a after the default handler.
	 * If 'after' is FALSE, the callback is run @a before the default handler, so the plugin
	 * can prevent Geany from processing the notification. Use this with care. */
        {"document-open", (GCallback) & on_document_action, FALSE, NULL},
        {"document-activate", (GCallback) & on_document_action, FALSE, NULL},
        {"document-save", (GCallback) & on_document_save, FALSE, NULL},
	{ "editor-notify", (GCallback) &on_editor_notify, FALSE, NULL },
        //{"document-before-save", (GCallback) & on_document_save, FALSE, NULL},
	{ NULL, NULL, FALSE, NULL }
};

static void menu_item_action(G_GNUC_UNUSED GtkMenuItem *menuitem, gpointer gdata)
{
    GeanyDocument *doc = document_get_current();
    document_save_file(doc, TRUE);
}

/* Called by Geany to initialize the plugin */
static gboolean demo_init(GeanyPlugin *plugin, gpointer data)
{
    
    GeanyData *geany_data = plugin->geany_data;
        geany_plugin_set_data(plugin, plugin, NULL);
	main_menu_item = gtk_menu_item_new_with_label(_("Format Python Code"));
	gtk_widget_show(main_menu_item);
	gtk_container_add(GTK_CONTAINER(geany->main_widgets->tools_menu), main_menu_item);
	g_signal_connect(main_menu_item, "activate", G_CALLBACK(menu_item_action), NULL);
	GeanyKeyGroup *group;
	group = plugin_set_key_group (plugin, _("Format Python Code"), KB_COUNT, NULL);
	keybindings_set_item (group, KB_FORMAT_PYCODE, NULL,
                        0, 0, "format_pycode", _("Format Python Code"), main_menu_item);
	//gchar *config_dir = g_build_path(G_DIR_SEPARATOR_S,
		//geany_data->app->configdir, "plugins", NULL);
	//gchar *pycoding_dbus_path = g_build_path(G_DIR_SEPARATOR_S, config_dir,
				    //GEANY_PYDBUS_BIN, NULL);
	//const gchar **argv = {pycoding_dbus_path,};

	//dbus_running = utils_spawn_sync(config_dir, *pycoding_dbus_path, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL);
        //g_free(config_dir);
	//g_free(pycoding_dbus_path);
	return TRUE;
}

/* Called by Geany before unloading the plugin.
 * Here any UI changes should be removed, memory freed and any other finalization done.
 * Be sure to leave Geany as it was before demo_init(). */
static void demo_cleanup(GeanyPlugin *plugin, gpointer data)
{
    g_dbus_connection_close_sync(jedi_connection, NULL, NULL);
    g_object_unref (jedi_connection);
    gtk_widget_destroy(main_menu_item);
}

void geany_load_module(GeanyPlugin *plugin)
{
	/* main_locale_init() must be called for your package before any localization can be done */
	main_locale_init(LOCALEDIR, GETTEXT_PACKAGE);
	plugin->info->name = _("Python Coding.");
	plugin->info->description = _("Python Completion, Checker and Formatter.");
	plugin->info->version = "0.1";
	plugin->info->author =  _("Sagar Chalise");

	plugin->funcs->init = demo_init;
	plugin->funcs->help = NULL; /* This demo has no help but it is an option */
	plugin->funcs->cleanup = demo_cleanup;
	plugin->funcs->callbacks = demo_callbacks;

	GEANY_PLUGIN_REGISTER(plugin, 225);
}