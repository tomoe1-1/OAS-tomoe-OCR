## 如何添加自定义庭院

1. 在`./tasks/Component/Costume`文件夹下面新建一个你的这个庭院皮肤文件夹比如`mainxxx`

2. 在你的文件夹下面新增一个`image.json`，启动`gui.py`对应工具添加对应的assets

   **很重要很重要，你不要乱划动庭院，如果你留意到游戏在你的停留位置是默认的**

   **在你生成之前需要点击探索然后在回来、再点击町中然后回来，确保你的当前的位置的默认的**

   你需要生成如下的assets:

   - check_main_xxx:   这张图片用来判断是否在庭院的
   - main_goto_exploration_xxx:  这张图片是用来点击"探索"图标的
   - main_goto_summon_xxx:  这张图片是用来点击 ”召唤“
   - main_goto_town_xxx:   町中知道吧
   - pet_house_xxx:  进入宠物屋

3. 执行`./dev_tools/assets_extract.py`这个将会更新`./tasks/Component/Costume/assets.py` 你不需要手动修改这个文件

4. 好了你现在以及输出你这个庭院皮肤的assets了，下面是为其加入 OAS 中

5. 打开`./tasks/Component/Costume/config.py`看到类似这种的，照着填进去

   ```python
   COSTUME_MAIN_2 = 'costume_main_2'  # 琼夜淬光
   ```

6. 打开`./tasks/Component/Costume/costume_base.py`看到类似这种的，照着填进去。

   解释一下，`COSTUME_MAIN_2` 是你刚刚定义的一个庭院类型，后面的第一个key就是原先的assets, 后面的value就是你刚刚输出的要替换的原先的assets。具体的值在`assets.py`文件有定义。照着填没毛病吧。

   ```python
   MainType.COSTUME_MAIN_2: {'I_CHECK_MAIN': 'I_CHECK_MAIN_1',
                                 'I_MAIN_GOTO_EXPLORATION': 'I_MAIN_GOTO_EXPLORATION_1',
                                 'I_MAIN_GOTO_SUMMON': 'I_MAIN_GOTO_SUMMON_1',
                                 'I_MAIN_GOTO_TOWN': 'I_MAIN_GOTO_TOWN_1',
                                 'I_PET_HOUSE': 'I_PET_HOUSE_1', },
   ```

7. 完事之后，你需要测试一下。执行`./tasks/Component/Costume/costume_test.py` 这个文件。

   **在测试之前你需要打开一次 OASX 修改你对应的庭院皮肤的选项**

8. 完事啦？？？哪有那么简单，你没有发现打开 OASX 的时候发现你的选项还是 `costume_main_xxx` 这种吗。你还需要添加一个翻译，[点击这儿](https://runhey.github.io/OnmyojiAutoScript-website/docs/development/user-option#7翻译)

## 动画庭院的处理（多帧）

有些庭院的判定部件和按钮是**带动画**的，单张模板会随帧跳动而失配。这类庭院要为每组 assets 采 3 帧（`_a` / `_b` / `_c`，顺序即匹配优先级），并在 `costume_base.py` 里把 value 写成**帧名列表**：

```python
main_costume_model[MainType.COSTUME_MAIN_18] = {
    'I_CHECK_MAIN': ['I_CHECK_MAIN_18_A', 'I_CHECK_MAIN_18_B', 'I_CHECK_MAIN_18_C'],
    # ... 其余四组同理
}
```

模型里写列表后，`_activate_main_costume()` 会自动走 `RuleGif.attach_to()` 把多帧匹配**原地挂载**到目标规则上（不换对象，持有引用的 Page 立即生效）；切回单帧庭院时会先 `RuleGif.detach_from()` 摘掉，避免残留的 GIF 方法继续按旧帧匹配。所以单帧庭院照旧按第 6 步写字符串即可，两种写法可以共存。

**每一帧都要有自己的 `roi_front`**：帧是从不同位置裁的，模板像素尺寸必须等于该帧 `roi_front` 的宽高，否则匹配直接崩（`tests/test_costume_main18.py` 会校验这一点）。

### 编号注意事项

本库的庭院编号与上游 `runhey/OnmyojiAutoScript` **错位一格**：

| 编号 | 本库 | runhey 上游 |
|---|---|---|
| 16 | 琦梦茨庭 | 狐栖归处 |
| 17 | 狐栖归处 | 玉岚狐庭 |
| 18 | 玉岚狐庭 | — |

也就是说上游同等素材在本库要**顺延一个编号**，不能直接按上游编号放，否则会拿 A 皮肤的模板当 B 皮肤用。目前本库所有庭院模板都是在自己的设备上重新截的（与上游无一字节相同），所以从上游取素材时**坐标必须连同模板一起搬**，不要只搬图。