"""Synthetic captured records only: these tests never execute Java/Maven/Gradle."""
import unittest

from agent_readiness.report import ReportError, _test_evidence

APPS = {'.': {'description': 'One synthetic Java application'}}
MAVEN_SUCCESS = '[INFO] Running ExampleTest\n[INFO] Tests run: 3, Failures: 0, Errors: 0, Skipped: 0\n[INFO] BUILD SUCCESS'
GRADLE_SUCCESS = '> Task :compileJava UP-TO-DATE\n> Task :test\nBUILD SUCCESSFUL in 1s'

def evidence(command, output):
    return {'kind': 'command', 'command': command, 'cwd': '.', 'exitCode': 0,
            'output': output, 'timeoutSeconds': 30}

class JavaCommandEvidenceGapTests(unittest.TestCase):
    def accepted(self, command, output):
        _test_evidence([evidence(command, output)], 1, APPS)

    def rejected(self, command, output):
        with self.assertRaises(ReportError):
            self.accepted(command, output)

    def test_maven_one_concrete_class_remains_valid(self):
        for selector in ('ExampleTest', 'org.example.ExampleTest'):
            with self.subTest(selector=selector):
                self.accepted('./mvnw -Dtest=' + selector + ' test', MAVEN_SUCCESS)
        self.accepted('./mvnw -f server/pom.xml -Dtest=ExampleTest test', MAVEN_SUCCESS)

    def test_gradle_one_concrete_class_remains_valid(self):
        self.accepted('./gradlew --rerun-tasks test --tests ExampleTest', GRADLE_SUCCESS)
        self.accepted('./gradlew --rerun-tasks :server:test --tests org.example.ExampleTest',
                      '> Task :server:test\nBUILD SUCCESSFUL')

    def test_maven_wildcard_is_not_one_selected_class(self):
        for selector in ('*', '*Test', 'Example*', '%regex[.*]', '!SlowTest'):
            with self.subTest(selector=selector):
                self.rejected('./mvnw -Dtest=' + selector + ' test', MAVEN_SUCCESS)

    def test_maven_comma_separated_classes_are_not_one_class(self):
        self.rejected('./mvnw -Dtest=ExampleTest,AnotherTest test', MAVEN_SUCCESS)

    def test_maven_repeated_selector_cannot_override_bounded_class(self):
        self.rejected('./mvnw -Dtest=ExampleTest -Dtest=* test', MAVEN_SUCCESS)

    def test_gradle_wildcard_is_not_one_selected_class(self):
        for selector in ('*', '*Test', 'Example*'):
            with self.subTest(selector=selector):
                self.rejected('./gradlew --rerun-tasks test --tests ' + selector, GRADLE_SUCCESS)

    def test_gradle_multiple_filters_are_not_one_class(self):
        self.rejected('./gradlew --rerun-tasks test --tests ExampleTest --tests AnotherTest', GRADLE_SUCCESS)

    def test_gradle_filter_must_have_a_class_argument(self):
        self.rejected('./gradlew --rerun-tasks test --tests --info', GRADLE_SUCCESS)

    def test_maven_any_failure_summary_blocks_a_success_summary(self):
        for option in ('-Dmaven.test.failure.ignore=true', '-fn', '--fail-never', ''):
            for counts in ('4, Failures: 1, Errors: 0', '4, Failures: 0, Errors: 1'):
                with self.subTest(option=option, counts=counts):
                    self.rejected('./mvnw -Dtest=ExampleTest ' + option + ' test',
                                  MAVEN_SUCCESS + '\n[ERROR] Tests run: ' + counts + ', Skipped: 0')

    def test_maven_explicitly_disabled_skip_does_not_hide_execution(self):
        for option in ('-DskipTests=false', '-Dmaven.test.skip=false'):
            with self.subTest(option=option):
                self.accepted('./mvnw -Dtest=ExampleTest ' + option + ' test', MAVEN_SUCCESS)

    def test_maven_equivalent_property_forms_are_still_bounded(self):
        for property_form in ('-D test=ExampleTest', '--define test=ExampleTest', '--define=test=ExampleTest'):
            with self.subTest(property_form=property_form):
                self.accepted('./mvnw ' + property_form + ' test', MAVEN_SUCCESS)
        for property_form in ('-D test=*', '--define test=*', '--define=test=*', '-Dtest=OtherTest'):
            with self.subTest(property_form=property_form):
                self.rejected('./mvnw -Dtest=ExampleTest ' + property_form + ' test', MAVEN_SUCCESS)
        for skip_form in ('-D skipTests', '--define maven.test.skip=true', '--define=skipTests=true'):
            with self.subTest(skip_form=skip_form):
                self.rejected('./mvnw -Dtest=ExampleTest ' + skip_form + ' test', MAVEN_SUCCESS)

if __name__ == '__main__':
    unittest.main()

# v2 removes an invalid positive containing unquoted shell $ expansion;
# the shared literal-command guard is outside this candidate scope.
