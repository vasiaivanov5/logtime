#-*- encoding: utf-8 -*-
import pantheradesktop.kernel
import sys
import os
import dbus
import ctypes
import dateutil.parser
import time
import pystache

try:
    from jira.client import JIRA
except ImportError:
    pass


__author__ = "Damian Kęska"
__license__ = "LGPLv3"
__maintainer__ = "Damian Kęska"
__copyright__ = "Copyleft by FINGO Team"


def runInstance(a=0):
    """ Run instance of application """

    kernel = logTimeKernel()
    kernel.appName = 'logtime'
    kernel.coreClasses['gui'] = False
    kernel.coreClasses['db'] = False
    kernel.coreClasses['argsparsing'] = logTimeArguments
    kernel.initialize(quiet=True)
    kernel.hooking.addOption('app.mainloop', kernel.mainLoop)
    kernel.main()

class XScreenSaverInfo( ctypes.Structure):

  """ typedef struct { ... } XScreenSaverInfo; """
  _fields_ = [('window',      ctypes.c_ulong), # screen saver window
              ('state',       ctypes.c_int),   # off,on,disabled
              ('kind',        ctypes.c_int),   # blanked,internal,external
              ('since',       ctypes.c_ulong), # milliseconds
              ('idle',        ctypes.c_ulong), # milliseconds
              ('event_mask',  ctypes.c_ulong)] # events


class logTimeArguments (pantheradesktop.argsparsing.pantheraArgsParsing):
    def setDebuggingMode(self, aaa = ''):
        """
            Enable debugging mode
        """

        self.panthera.logging.silent = False
        self.panthera.logging.flushAndEnablePrinting()

    def setDate(self, date = ''):
        """
        Set date for JIRA tickets, default value is today date
        :param date:
        :return:
        """

        if not date or date == 'today':
            date = time.strftime("%d.%m.%Y")

        self.panthera._ticketsDate = dateutil.parser.parse(date).strftime('%s')


    def printJIRATickets(self, action = ''):
        """
        Run in mode to just print JIRA tickets
        :param action:
        :return:
        """

        self.panthera._printJIRATickets = True

    def addArgs(self):
        pantheradesktop.argsparsing.pantheraArgsParsing(self)
        self.createArgument('--debug', self.setDebuggingMode, '', 'Enable debugging mode', required=False, action='store_false')
        self.createArgument('--get-jira-tickets', self.printJIRATickets, '', 'Print JIRA tickets', required=False, action='store_false')



class logTimeKernel (pantheradesktop.kernel.pantheraDesktopApplication, pantheradesktop.kernel.Singleton):
    """
    Main class that contains mainLoop() method which is called as first right after parsing the arguments

    :author: Damian Kęska <damian@pantheraframework.org>
    """

    jira = None # jira object
    jiraLogin = ""
    profile = None
    template = "Work in {{date}}\n\n{{#issues}}\n{{config.jira_serverURL}}/browse/{{.}}\n{{/issues}}"
    _printJIRATickets = False
    _ticketsDate = "today"
    _JIRATemplate = "default"

    def printJIRATickets(self):
        """
        Print JIRA tickets
        :return string:
        """

        ## prepare settings
        if self._JIRATemplate == 'default':
            self._JIRATemplate = self.filesDir + '/jira-template.tpl'

            if not os.path.isfile(self._JIRATemplate):
                w = open(self._JIRATemplate, 'w')
                w.write(self.template)
                w.close()

        # check template file access
        if os.path.isfile(self._JIRATemplate) and os.access(self._JIRATemplate, os.R_OK):
            self.template = open(self._JIRATemplate, 'r').read()
            self.logging.output('JIRA template loaded from '+self._JIRATemplate, 'issuesChecker')
        else:
            self.logging.output('Cannot read JIRA template from '+self._JIRATemplate, 'issuesChecker')

        if self._ticketsDate == 'today':
            self._ticketsDate = time.strftime("%d.%m.%Y")



        ## connection options
        options = {
            'server': self.config.getKey('jira.serverURL', 'https://example.org/jira')
        }

        if options['server'] == 'https://example.org/jira':
            print("Please update ~/.logtime/config.json with server, user name and password (if required)\n")
            sys.exit(0)

        ## authorization
        basic_auth = None

        if self.config.getKey('jira.user', '') and self.config.getKey('jira.password'):
            basic_auth = (self.config.getKey('jira.user', ''), self.config.getKey('jira.password'))
            self.logging.output('Using authorization for "'+str(self.config.getKey('jira.user'))+'" user')

        self.jira = JIRA(basic_auth=basic_auth, options=options)
        self.profile = self.jira.search_users(self.jira.current_user())
        self.jiraLogin = self.jira.current_user()
        projects = self.config.getKey('jira.projects')


        ## monitor all projects user has access to by default
        if not self.config.getKey('jira.projects'):
            for project in self.jira.projects():
                projects.append(str(project.key))

            self.config.setKey('jira.projects', projects)


        ## build and execute a JQL query
        jql = self.config.getKey('jira.projects.jql', 'updated >= -1d AND (creator in ({user}) OR assignee in ({user}) OR Responsible in ({user})) AND {projects} ORDER BY updated DESC')
        jql = jql.replace('{projects}', self.projectsArrayToJQL(projects))
        jql = jql.replace('{user}', self.jira.current_user())
        self.logging.output('Executing JQL query: '+jql, 'issuesChecker')

        ## results
        issues = []
        issuesToLookup = self.jira.search_issues(jql, expand='changelog')
        issueLinks = ""

        self.logging.output('Found '+str(len(issuesToLookup))+' issues', 'issuesChecked')

        for issue in issuesToLookup:
            self.logging.output('Checking issue '+str(issue.key), 'issuesChecker')

            found = False

            for history in issue.changelog.histories:
                historyDate = dateutil.parser.parse(history.created).strftime('%d.%m.%Y')

                if historyDate == self._ticketsDate and history.author.name == self.jiraLogin:
                    found = True
                    break

            if found:
                self.logging.output('Found matching issue: '+issue.key+' with date '+historyDate, 'issuesChecker')
                issues.append(issue.key)



        config = dict()

        for key,value in self.config.memory.iteritems():
            config[key.replace('.', '_')] = value

        ## render template
        rendered = pystache.render(self.template, {
            'date': dateutil.parser.parse(self._ticketsDate).strftime(self.config.getKey('jira.dateFormat', '%d.%m.%Y', strictTypeChecking = True)),
            'config': config,
            'issues': issues
        })


        print(rendered)


    def monitorInactivityTime(self):
        """
        Monitor user activity and
        :return:
        """

        if not "DISPLAY" in os.environ:
            print('Cannot find $DISPLAY, is X11 server running?')
            sys.exit(1)

        configIdleTime = self.config.getKey('inactivity.idletime', 300, strictTypeChecking = True)

        try:
            xlib = ctypes.cdll.LoadLibrary( 'libX11.so')
        except Exception:
            print('Cannot load shared library libX11.so, please make sure you have installed libXss library')
            sys.exit(1)

        try:
            self.xss = ctypes.cdll.LoadLibrary( 'libXss.so')
        except Exception:
            print('Cannot load shared library libX11.so, please make sure you have installed libXss library')
            sys.exit(1)

        dpy = xlib.XOpenDisplay(os.environ['DISPLAY'])
        root = xlib.XDefaultRootWindow( dpy)
        self.xss.XScreenSaverAllocInfo.restype = ctypes.POINTER(XScreenSaverInfo)

        while True:
            time.sleep(1)
            xss_info = self.xss.XScreenSaverAllocInfo()
            self.xss.XScreenSaverQueryInfo( dpy, root, xss_info)
            idleTime = int(int(xss_info.contents.idle)/1000)

            if idleTime > configIdleTime:
                self.idleTimeAction(idleTime)


    def idleTimeAction(self, idleTime):
        """
        Execute action when computer gets idle for longer time (inactivity.idletime in config)
        :param idleTime:
        :return:
        """

        self.hooking.execute('inactivity.idle.run', idleTime)

        try:
            bus = dbus.SessionBus()
            object = bus.get_object("org.freedesktop.ScreenSaver", "/org/freedesktop/ScreenSaver")
            interface = dbus.Interface(object, 'org.freedesktop.ScreenSaver')

            if not int(interface.GetActive()):
                interface.Lock()

        except Exception as e:
            self.logging.output('Got exception while trying to lock screen: '+str(e), 'inactivity')
            print('Cannot connect to screensaver, please make sure you are running XScreenSaver, KScreensaver or GNOME Screensaver, or any other compatible with freedesktop interface')
            sys.exit(1)



    def mainLoop(self, a=''):
        """ Application's main function """

        ## mode that will print JIRA tickets user was working on in selected date or today
        if self._printJIRATickets:
            if not "JIRA" in globals():
                print('No python-jira installed to use this feature, exiting...')
                sys.exit(1)

            self.printJIRATickets()
            sys.exit(0)

        self.monitorInactivityTime()




    def projectsArrayToJQL(self, projects):
        """
        Convert list type to JQL string
        :param projects: Projects list
        :author: Damian Kęska <damian@pantheraframework.org>
        :return string: JQL query string
        """

        projectsJQLString = '('

        for project in projects:
            projectsJQLString += 'OR project = "'+project+'" '

        projectsJQLString += ')'
        return projectsJQLString.replace('(OR', '(')
