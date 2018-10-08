all: build
	
build:	
	gcc -DLOCALEDIR=\"\" -DGETTEXT_PACKAGE=\"pycoding\" -c ./pycoding.c -fPIC `pkg-config --cflags geany libsoup-2.4`
	gcc pycoding.o -o pycoding.so -shared `pkg-config --libs geany libsoup-2.4`

install: uninstall startinstall

startinstall:
	cp -f ./pycoding.{so,py} ~/.config/geany/plugins
	chmod 755 ~/.config/geany/plugins/pycoding.{so,py}

uninstall:
	rm -f ~/.config/geany/plugins/pycoding.{so,py}

clean:
	rm -f ./pycoding.so
	rm -f ./pycoding.o
