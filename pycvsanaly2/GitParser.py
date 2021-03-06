# Copyright (C) 2007 LibreSoft
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 59 Temple Place - Suite 330, Boston, MA 02111-1307, USA.
#
# Authors :
#       Carlos Garcia Campos <carlosgc@gsyc.escet.urjc.es>

import os
import re
import time
import datetime

from Parser import Parser
from Repository import Commit, Action, Person
from utils import printout, printdbg

class GitParser (Parser):

    class GitCommit:

        def __init__ (self, commit, parents):
            self.commit = commit
            self.parents = parents
            self.svn_tag = None

        def is_my_child (self, git_commit):
            return git_commit.parents and self.commit.revision in git_commit.parents

    class GitBranch:

        ( REMOTE,
          LOCAL,
          STASH ) = range (3)

        def __init__ (self, type, name, tail):
            self.type = type
            self.name = name
            self.set_tail (tail)

        def is_my_parent (self, git_commit):
            return git_commit.is_my_child (self.tail)

        def is_remote (self):
            return self.type == self.REMOTE

        def is_local (self):
            return self.type == self.LOCAL

        def is_stash (self):
            return self.type == self.STASH

        def set_tail (self, tail):
            self.tail = tail
            self.tail.commit.branch = self.name

    patterns = {}
    patterns['commit'] = re.compile ("^commit[ \t]+([^ ]+)( ([^\(]+))?( \((.*)\))?$")
    patterns['author'] = re.compile ("^Author:[ \t]+(.*)[ \t]+<(.*)>$")
    patterns['committer'] = re.compile ("^Commit:[ \t]+(.*)[ \t]+<(.*)>$")
    patterns['date'] = re.compile ("^CommitDate: (.* [0-9]+ [0-9]+:[0-9]+:[0-9]+ [0-9][0-9][0-9][0-9]) ([+-][0-9][0-9][0-9][0-9])$")
    patterns['file'] = re.compile ("^([MAD])[ \t]+(.*)$")
    patterns['file-moved'] = re.compile ("^([RC])[0-9]+[ \t]+(.*)[ \t]+(.*)$")
    patterns['branch'] = re.compile ("refs/remotes/origin/([^,]*)")
    patterns['local-branch'] = re.compile ("refs/heads/([^,]*)")
    patterns['tag'] = re.compile ("tag: refs/tags/([^,]*)")
    patterns['stash'] = re.compile ("refs/stash")
    patterns['ignore'] = [re.compile ("^AuthorDate: .*$"), re.compile ("^Merge: .*$")]
    patterns['svn-tag'] = re.compile ("^svn path=/tags/(.*)/?; revision=([0-9]+)$")

    def __init__ (self):
        Parser.__init__ (self)

        self.is_gnome = None

        # Parser context
        self.commit = None
        self.branch = None
        self.branches = []

    def set_repository (self, repo, uri):
        Parser.set_repository (self, repo, uri)
        self.is_gnome = re.search ("^[a-z]+://(.*@)?git\.gnome\.org/.*$", repo.get_uri ()) is not None

    def flush (self):
        if self.branches:
            if self.branch.is_remote ():
                self.handler.commit (self.branch.tail.commit)
            self.branch = None
            self.branches = None

    def _parse_line (self, line):
        if line is None or line == '':
            return

        # Ignore
        for patt in self.patterns['ignore']:
            if patt.match (line):
                return

        # Commit
        match = self.patterns['commit'].match (line)
        if match:
            if self.commit is not None and self.branch.is_remote ():
                if self.branch.tail.svn_tag is None: # Skip commits on svn tags
                    self.handler.commit (self.branch.tail.commit)

            self.commit = Commit ()
            self.commit.revision = match.group (1)

            parents = match.group (3)
            if parents:
                parents = parents.split ()
            git_commit = self.GitCommit (self.commit, parents)

            decorate = match.group (5)
            branch = None
            if decorate:
                # Remote branch
                m = re.search (self.patterns['branch'], decorate)
                if m:
                    branch = self.GitBranch (self.GitBranch.REMOTE, m.group (1), git_commit)
                    printdbg ("Branch '%s' head at acommit %s", (branch.name, self.commit.revision))
                else:
                    # Local Branch
                    m = re.search (self.patterns['local-branch'], decorate)
                    if m:
                        branch = self.GitBranch (self.GitBranch.LOCAL, m.group (1), git_commit)
                        printdbg ("Commit %s on local branch '%s'", (self.commit.revision, branch.name))
                        # If local branch was merged we just ignore this decoration
                        if self.branch and self.branch.is_my_parent (git_commit):
                            printdbg ("Local branch '%s' was merged", (branch.name,))
                            branch = None
                    else:
                        # Stash
                        m = re.search (self.patterns['stash'], decorate)
                        if m:
                            branch = self.GitBranch (self.GitBranch.STASH, "stash", git_commit)
                            printdbg ("Commit %s on stash", (self.commit.revision,))
                # Tag
                m = re.search (self.patterns['tag'], decorate)
                if m:
                    self.commit.tags = [m.group (1)]
                    printdbg ("Commit %s tagged as '%s'", (self.commit.revision, self.commit.tags[0]))

            if branch is not None and self.branch is not None:
                # Detect empty branches. Ideally, the head of a branch
                # can't have children. When this happens is because the
                # branch is empty, so we just ignore such branch
                if self.branch.is_my_parent (git_commit):
                    printout ("Warning: Detected empty branch '%s', it'll be ignored", (branch.name,))
                    branch = None

            if len (self.branches) >= 2:
                # If current commit is the start point of a new branch
                # we have to look at all the current branches since
                # we haven't inserted the new branch yet.
                # If not, look at all other branches excluding the current one
                for i, b in enumerate (self.branches):
                    if i == 0 and branch is None:
                        continue

                    if b.is_my_parent (git_commit):
                        # We assume current branch is always the last one
                        # AFAIK there's no way to make sure this is right
                        printdbg ("Start point of branch '%s' at commit %s", (self.branches[0].name, self.commit.revision))
                        self.branches.pop (0)
                        self.branch = b

            if self.branch and self.branch.tail.svn_tag is not None and self.branch.is_my_parent (git_commit):
                # There's a pending tag in previous commit
                pending_tag = self.branch.tail.svn_tag
                printdbg ("Move pending tag '%s' from previous commit %s to current %s", (pending_tag,
                                                                                          self.branch.tail.commit.revision,
                                                                                          self.commit.revision))
                if self.commit.tags and pending_tag not in self.commit.tags:
                    self.commit.tags.append (pending_tag)
                else:
                    self.commit.tags = [pending_tag]
                self.branch.tail.svn_tag = None

            if branch is not None:
                self.branch = branch

                # Insert master always at the end
                if branch.is_remote () and branch.name == 'master':
                    self.branches.append (self.branch)
                else:
                    self.branches.insert (0, self.branch)
            else:
                self.branch.set_tail (git_commit)

            return

        # Committer
        match = self.patterns['committer'].match (line)
        if match:
            self.commit.committer = Person ()
            self.commit.committer.name = match.group (1)
            self.commit.committer.email = match.group (2)
            self.handler.committer (self.commit.committer)

            return

        # Author
        match = self.patterns['author'].match (line)
        if match:
            self.commit.author = Person ()
            self.commit.author.name = match.group (1)
            self.commit.author.email = match.group (2)
            self.handler.author (self.commit.author)

            return

        # Date
        match = self.patterns['date'].match (line)
        if match:
            self.commit.date = datetime.datetime (* (time.strptime (match.group (1).strip (" "), "%a %b %d %H:%M:%S %Y")[0:6]))
            # datetime.datetime.strptime not supported by Python2.4
            #self.commit.date = datetime.datetime.strptime (match.group (1).strip (" "), "%a %b %d %H:%M:%S %Y")
            
            return

        # File
        match = self.patterns['file'].match (line)
        if match:
            action = Action ()
            action.type = match.group (1)
            action.f1 = match.group (2)

            self.commit.actions.append (action)
            self.handler.file (action.f1)
        
            return

        # File moved/copied
        match = self.patterns['file-moved'].match (line)
        if match:
            action = Action ()
            type = match.group (1)
            if type == 'R':
                action.type = 'V'
            else:
                action.type = type
            action.f1 = match.group (3)
            action.f2 = match.group (2)
            action.rev = self.commit.revision

            self.commit.actions.append (action)
            self.handler.file (action.f1)

            return

        # This is a workaround for a bug in the GNOME Git migration
        # There are commits on tags not correctly detected like this one:
        # http://git.gnome.org/cgit/evolution/commit/?id=b8e52acac2b9fc5414a7795a73c74f7ee4eeb71f
        # We want to ignore commits on tags since it doesn't make any sense in Git
        if self.is_gnome:
            match = self.patterns['svn-tag'].match (line.strip ())
            if match:
                printout ("Warning: detected a commit on a svn tag: %s", (match.group (0),))
                tag = match.group (1)
                if self.commit.tags and tag in self.commit.tags:
                    # The commit will be ignored, so move the tag
                    # to the next (previous in history) commit
                    self.branch.tail.svn_tag = tag

        # Message
        self.commit.message += line + '\n'

        assert True, "Not match for line %s" % (line)
