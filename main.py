from image_generator import generate_recipe_assets_from_idea_file


def main() -> None:
    print("开始执行一页厨完整生成流程，请等待接口返回结果...")

    try:
        result = generate_recipe_assets_from_idea_file(idea_file_name="dish_name.txt")
    except Exception as exc:
        print(f"运行失败：{exc}")
        raise SystemExit(1) from exc

    print("图片生成完成")
    print(f"本次创意输入：{result['dish_idea']}")
    print(f"本次最终菜名：{result['dish_name']}")
    print(f"输出根目录：{result['output_root']}")
    print(f"创意菜谱文件：{result['creative_file']}")
    print(f"文生图 prompt 文件：{result['prompt_file']}")
    for saved_file in result["saved_files"]:
        print(f"已保存：{saved_file}")


if __name__ == "__main__":
    main()
