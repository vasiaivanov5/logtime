#-*- encoding: utf-8 -*-
import pantheradesktop.kernel
import pantheradesktop.tools as tools
import sys
import os
import dbus
import gobject
from dbus.mainloop.glib import DBusGMainLoop
import ctypes
import dateutil.parser
import time

try:
    import pystache
except ImportError:
    pass

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

    def monitorInactivity(self, value = None):
        """
        Set mode to monitor user inactivity

        :param bool value: Default value
        """

        self.panthera._monitorInactivity = True

    def displayBreakTime(self, value = None):
        """
        Set mode to display notifications about break time

        :param bool value: Default value
        """

        self.panthera._breakTime = True

    def addArgs(self):
        pantheradesktop.argsparsing.pantheraArgsParsing(self.app)
        self.createArgument('--get-jira-tickets', self.printJIRATickets, '', 'Print JIRA tickets that you worked on (today or on selected date)', required=False, action='store_false')
        self.createArgument('--date', self.setDate, '', 'Set date for JIRA tickets', required=False, action='store')
        self.createArgument('--monitor-inactivity', self.monitorInactivity, '', 'Monitor inactivity and lock screen when ide time reaches maximum time specified in configuration key "inactivity.idletime" (unit: seconds)', required=False, action='store_false')
        self.createArgument('--display-break-time', self.displayBreakTime, '', 'Show notifications on desktop after break time', required=False, action='store_false')



class logTimeKernel (pantheradesktop.kernel.pantheraDesktopApplication, pantheradesktop.kernel.Singleton):
    """
    Main class that contains mainLoop() method which is called as first right after parsing the arguments

    :author: Damian Kęska <damian@pantheraframework.org>
    """

    threads = dict()

    ## JIRA tickets listing
    jira = None # jira object
    jiraLogin = ""
    profile = None
    template = "Work in {{date}}\n\n{{#issues}}\n{{config.jira_serverURL}}/browse/{{.}}\n{{/issues}}"
    _printJIRATickets = False
    _ticketsDate = "today"
    _JIRATemplate = "default"

    ## monitoring activity
    _monitorInactivity = False
    xss = None
    xssDpy = None
    xssRoot = None

    ## break time
    _breakTime = False
    screensaverlastState = None
    screensaverTime = 0

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

    def prepareInactivityTimeMonitoring(self):
        if not "DISPLAY" in os.environ:
            print('Cannot find $DISPLAY, is X11 server running?')
            sys.exit(1)

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

        self.xssDpy = xlib.XOpenDisplay(os.environ['DISPLAY'])
        self.xssRoot = xlib.XDefaultRootWindow(self.xssDpy)
        self.xss.XScreenSaverAllocInfo.restype = ctypes.POINTER(XScreenSaverInfo)

    def monitorInactivityTime(self, thread):
        """
        Monitor user activity and
        :return:
        """

        configIdleTime = self.config.getKey('inactivity.idletime', 300, strictTypeChecking = True)
        self.logging.output('Monitoring user inactivity', 'monitorInactivity')

        while True:
            time.sleep(1)
            xss_info = self.xss.XScreenSaverAllocInfo()
            self.xss.XScreenSaverQueryInfo(self.xssDpy, self.xssRoot, xss_info)
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
            self.bus = dbus.SessionBus()
            object = self.bus.get_object("org.freedesktop.ScreenSaver", "/org/freedesktop/ScreenSaver")
            interface = dbus.Interface(object, 'org.freedesktop.ScreenSaver')

            if not int(interface.GetActive()):
                interface.Lock()

        except Exception as e:
            self.logging.output('Got exception while trying to lock screen: '+str(e), 'inactivity')
            print('Cannot connect to screensaver, please make sure you are running XScreenSaver, KScreensaver or GNOME Screensaver, or any other compatible with freedesktop interface')
            sys.exit(1)


    def calculateBreakTime(self, thread):
        """
        Calculate break time when user gets idle and then display a notification
        :return:
        """

        loop = gobject.MainLoop()
        dbus.set_default_main_loop(DBusGMainLoop(set_as_default=True))

        bus = dbus.SessionBus()
        bus.add_signal_receiver(self.screensaverChangedEvent,'ActiveChanged','org.freedesktop.ScreenSaver')
        loop.run()


    def screensaverChangedEvent(self, state):
        """
        Handle screensaver state change event
        :param bool state: 1 or 0
        :return: None
        """

        if not self.screensaverlastState and state:
            self.screensaverTime = time.time()
        elif self.screensaverlastState and not state:
            breakTime = (time.time()-self.screensaverTime)

            self.hooking.execute('breaktime.time', breakTime)

            if breakTime > 60:
                breakTime = str(int(breakTime/60)) + ' minutes'
            else:
                breakTime = str(int(breakTime)) + ' seconds'

            os.system('notify-send "logtime" "Your break time is: '+breakTime+'"')

        self.screensaverlastState = state


    def mainLoop(self, a=''):
        """ Application's main function """

        ## mode that will print JIRA tickets user was working on in selected date or today
        if self._printJIRATickets:
            if not "JIRA" in globals():
                print('No python-jira installed to use this feature, exiting...')
                sys.exit(1)

            if not "pystache" in globals():
                print('No pystached installed to use this feature, exiting...')
                sys.exit(1)

            self.printJIRATickets()
            sys.exit(0)

        if self._breakTime:
            t1, w2 = tools.createThread(self.calculateBreakTime)

        if self._monitorInactivity:
            self.prepareInactivityTimeMonitoring()
            t2, w2 = tools.createThread(self.monitorInactivityTime)

        if not self._monitorInactivity and not self._breakTime:
            print('No action selected')
            sys.exit(0)

        while True:
            time.sleep(1000)




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

