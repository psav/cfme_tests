import os
import re
import click
from lxml import etree
from collections import defaultdict
from operator import attrgetter


template = """
<html>
  <head>
    <style>
    .passed {background-color:green}
    .failed {background-color:orange}
    .skipped {background-color:blue}
    .not_added {background-color:yellow}
    .blocked {background-color:blue}
    .bad_test {background-color:red}
    </style>
  </head>
  <body>
    <table class="table table-striped"></table>
  </body>
</html>
"""


class MissingObject(object):
    pass


class WorkItemCache(object):
    def __init__(self, repo_dir):
        self.repo_dir = repo_dir
        self.test_run_dir = self.repo_dir + '/testing/testruns/'
        self.test_case_dir = self.repo_dir + '/tracker/workitems/'
        self._cache = defaultdict(dict)

    @staticmethod
    def get_path(num):
        """Gets a path from the workitem number

        For example: 31942 will return 30000-39999/31000-31999/31900-31999
        """
        num = int(num)
        dig_len = len(str(num))
        paths = []
        for i in range(dig_len - 2):
            divisor = 10 ** (dig_len - i - 1)
            paths.append(
                "{}-{}".format((num / divisor) * divisor, (((num / divisor) + 1) * divisor) - 1))
        return "/".join(paths)

    def __getitem__(self, work_item_id):
        if work_item_id not in self._cache:
            prefix, tcid = work_item_id.split("-")
            path = self.test_case_dir + self.get_path(tcid) + "/" + work_item_id + "/workitem.xml"
            try:
                tree = etree.parse(path)
            except:
                print "WARNING: Couldn't load case {}".format(work_item_id)
                self._cache[work_item_id] = MissingObject()
                return
            for item in tree.xpath('/work-item/field'):
                self._cache[work_item_id][item.attrib['id']] = item.text
            if self._cache[work_item_id]['type'] == 'testcase':
                if 'assignee' not in self._cache[work_item_id]:
                    self._cache[work_item_id]['assignee'] = "Unassigned"
        elif isinstance(self._cache[work_item_id], MissingObject):
            return None
        return self._cache[work_item_id]


class TestCase(object):
    def __init__(self, tc_id, title, params=None):
        self.tc_id = tc_id
        self.title = title
        self.params = params or {}

    def __eq__(self, y):
        return self.__dict__ == y.__dict__

    def __hash__(self):
        return hash(str(self.__dict__))


class PolarionReporter(object):
    def __init__(self, repo_dir):
        self.repo_dir = repo_dir
        self.test_run_dir = self.repo_dir + '/testing/testruns/'
        self.test_case_dir = self.repo_dir + '/tracker/workitems/'
        self.wi_cache = WorkItemCache(self.repo_dir)

    def collate_runs(self, run_id=None, group_id=None):
        runs = []
        results = defaultdict(dict)
        c = 0
        for trfile in os.listdir(self.test_run_dir):
            try:
                tree = etree.parse(self.test_run_dir + trfile + "/testrun.xml")
            except Exception as e:
                print e
                print "failed"
            group = tree.xpath('/test-run/field[@id="groupId"]')
            if group_id and (not group or not re.match(group_id, group[0].text)):
                continue
            if run_id and trfile != run_id:
                continue
            # print "found", trfile
            runs.append(trfile)
            for result in tree.xpath('/test-run/field[@id="records"]/list/struct'):
                tc_id = result.xpath('item[@id="testCase"]')[0].text
                param_dict = {}
                params = result.xpath('item[@id="testParameters"]/list/struct')
                for param in params:
                    try:
                        param_dict[
                            param.xpath('item[@id="name"]')[0].text] = param.xpath(
                                'item[@id="rawValue"]')[0].text
                    except:
                        print "WARNING: Test Case {} was malformed".format(tc_id)

                if not self.wi_cache[tc_id]:
                    continue
                if 'title' not in self.wi_cache[tc_id]:
                    continue
                testcase_obj = TestCase(tc_id, self.wi_cache[tc_id]['title'], params=param_dict)

                # if user_filter and not cases[tc_id]['assignee'] == user_filter:
                #    continue
                res = result.xpath('item[@id="result"]')
                if res:
                    real_res = res[0].text
                else:
                    real_res = "skipped"

                results[testcase_obj][trfile] = real_res
                c += 1
        print "Processed {} unique results".format(c)
        return runs, results

    def generate_report(self, output, run_id=None, group_id=None, user_filter=None,
                        only_unexecuted=False):
        runs, results = self.collate_runs(group_id=group_id, run_id=run_id)
        runs = sorted(runs, reverse=True)
        the_html = etree.fromstring(template)
        body = the_html.find('body')
        table = body.find('table')
        header_row = etree.Element("tr")
        tc_title = etree.Element("td")
        tc_title.text = "Test Cases"
        header_row.append(tc_title)
        comp_title = etree.Element("td")
        comp_title.text = "Importance"
        header_row.append(comp_title)
        comp_title = etree.Element("td")
        comp_title.text = "Assignee"
        header_row.append(comp_title)
        comp_title = etree.Element("td")
        comp_title.text = "Composite"
        header_row.append(comp_title)
        param_title = etree.Element("td")
        param_title.text = "Parameters"
        header_row.append(param_title)
        for run in runs:
            run_title = etree.Element("td")
            run_title.text = run
            header_row.append(run_title)
        table.append(header_row)

        for test_case in sorted(results.keys(), key=attrgetter('tc_id')):
            test_case_id = test_case.tc_id
            test_case_title = test_case.title
            params = test_case.params
            try:
                if user_filter and not self.wi_cache[test_case_id]['assignee'] == user_filter:
                    continue
            except:
                print 'WARNING: Malformed Test Case: {}'.format(test_case_id)
            case_row = etree.Element("tr")
            case_title = etree.Element("td")
            case_title.text = "{} ({})".format(
                test_case_id, test_case_title.encode('ascii', 'ignore'))
            case_row.append(case_title)
            results[test_case]['composite'] = "N/A"
            for run in runs:
                res = results[test_case].get(run, "not_added")
                res_title = etree.Element("td")
                res_title.attrib['class'] = res
                res_title.text = res
                case_row.append(res_title)
                if results[test_case]['composite'] is "N/A":
                    if res not in ["not_added", "skipped"]:
                        results[test_case]['composite'] = res
            if only_unexecuted and results[test_case]['composite'] is not "N/A":
                continue
            composite_res = etree.Element("td")
            composite_res.text = results[test_case]['composite']
            composite_res.attrib['class'] = results[test_case]['composite']
            importance = etree.Element("td")
            importance.text = self.wi_cache[test_case_id].get('caseimportance')
            importance = etree.Element("td")
            importance.text = self.wi_cache[test_case_id].get('caseimportance')
            assignee = etree.Element("td")
            assignee.text = self.wi_cache[test_case_id].get('assignee')
            if (results[test_case]['composite'] == "N/A" and
                    self.wi_cache[test_case_id].get('caseimportance') in ['high', 'critical']):
                case_title.attrib['class'] = 'bad_test'
            param_res = etree.Element("td")
            for param, value in params.iteritems():
                t_param = etree.Element("strong")
                t_param.text = "{}: ".format(param)
                param_res.append(t_param)
                v_param = etree.Element("em")
                v_param.text = value
                param_res.append(v_param)
                lb = etree.Element("br")
                param_res.append(lb)

            case_row.insert(1, importance)
            case_row.insert(2, assignee)
            case_row.insert(3, composite_res)
            case_row.insert(4, param_res)

            table.append(case_row)
        body.append(table)
        xml = etree.ElementTree(the_html)
        xml.write(output, pretty_print=True)

        print "Processed {} test runs".format(len(runs))
        print "Processed {} test cases".format(len(results))


@click.command(help="Assist in generating release changelog")
@click.argument('svn-dir')
@click.argument('output')
@click.option('--group-id')
@click.option('--run-id', default=None)
@click.option('--user-filter', default=None,
              help="Filter report by user")
@click.option('--only-unexecuted', default=False, is_flag=True)
def main(svn_dir, user_filter, group_id, output, run_id, only_unexecuted):
    """Main script"""
    reporter = PolarionReporter(svn_dir)
    reporter.generate_report(output, group_id=group_id, run_id=run_id, user_filter=user_filter,
                             only_unexecuted=only_unexecuted)


if __name__ == "__main__":
    main()
