// Run in a private Fcitx profile. Only the fixed test string enters the engine.
#include <fcitx-utils/eventdispatcher.h>
#include <fcitx/addonmanager.h>
#include <fcitx/candidatelist.h>
#include <fcitx/event.h>
#include <fcitx/inputcontext.h>
#include <fcitx/inputcontextmanager.h>
#include <fcitx/inputmethodgroup.h>
#include <fcitx/inputmethodmanager.h>
#include <fcitx/inputpanel.h>
#include <fcitx/instance.h>

#include <iostream>
#include <string>

class ProbeContext final : public fcitx::InputContext {
public:
    explicit ProbeContext(fcitx::InputContextManager &manager)
        : InputContext(manager, "eidolon-ime-probe") {
        created();
        setCapabilityFlags(fcitx::CapabilityFlag::Preedit);
    }

    ~ProbeContext() override { destroy(); }

    void press(const std::string &name) {
        for (bool release : {false, true}) {
            fcitx::KeyEvent event(this, fcitx::Key(name), release);
            keyEvent(event);
        }
    }

    std::string committed;

private:
    const char *frontend() const override { return "eidolon-probe"; }
    void commitStringImpl(const std::string &text) override { committed += text; }
    void forwardKeyImpl(const fcitx::ForwardKeyEvent &) override {}
    void deleteSurroundingTextImpl(int, unsigned int) override {}
    void updatePreeditImpl() override {}
};

int main(int argc, char **argv) {
    fcitx::Instance instance(argc, argv);
    instance.addonManager().registerDefaultLoader(nullptr);
    bool passed = false;
    instance.eventDispatcher().schedule([&] {
        if (!instance.addonManager().addon("mozc", true)) {
            std::cerr << "Mozc addon did not load\n";
            instance.exit();
            return;
        }

        auto &manager = instance.inputMethodManager();
        auto group = manager.currentGroup();
        group.inputMethodList().clear();
        group.inputMethodList().emplace_back("mozc");
        group.setDefaultInputMethod("mozc");
        manager.setGroup(std::move(group));

        ProbeContext context(instance.inputContextManager());
        context.focusIn();
        instance.setCurrentInputMethod(&context, "mozc", true);
        for (char key : std::string("nihongo")) {
            context.press(std::string(1, key));
        }
        auto reading = context.inputPanel().clientPreedit().toString();
        context.press("space");
        auto converted = context.inputPanel().clientPreedit().toString();
        context.press("space");
        auto list = context.inputPanel().candidateList();
        int count = list ? list->size() : 0;
        bool found = false;
        for (int index = 0; index < count; ++index) {
            const auto &candidate = list->candidate(index);
            if (candidate.text().toString() == "日本語") {
                found = true;
                candidate.select(&context);
                break;
            }
        }
        if (context.committed.empty()) {
            context.press("Return");
        }
        std::cout << "reading=" << reading << " conversion=" << converted
                  << " candidates=" << count << " committed=" << context.committed
                  << '\n';
        passed = reading == "にほんご" && found && count > 1 &&
                 context.committed == "日本語";
        instance.exit();
    });
    instance.exec();
    return passed ? 0 : 1;
}
